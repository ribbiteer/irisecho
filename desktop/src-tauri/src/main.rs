// SPDX-License-Identifier: AGPL-3.0-or-later
//! IrisEcho desktop shell.
//!
//! The installer carries three things: this shell, a `uv` binary and the
//! IrisEcho core as a Python wheel (with the web UI inside). On first launch
//! the shell uses `uv` to create a private Python environment under the data
//! folder and installs the core into it; later launches reuse it. The shell
//! then starts the core on a free local port and points its window at it.
//! The core watches its stdin: when this shell exits for any reason, the pipe
//! closes and the core (and every engine it started) shuts down.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const PYTHON: &str = "3.12";

#[derive(Clone, Serialize, Default)]
struct BootStatus {
    stage: String, // "preparing" | "installing" | "starting" | "ready" | "error"
    message: String,
    lines: Vec<String>,
}

#[derive(Default)]
struct Shell {
    core: Mutex<Option<Child>>,
    status: Arc<Mutex<BootStatus>>,
}

// --- paths -------------------------------------------------------------------

fn home() -> PathBuf {
    std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .map(PathBuf::from)
        .unwrap_or_default()
}

/// Must match irisecho_core.paths.data_dir().
fn data_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("IRISECHO_HOME") {
        return PathBuf::from(dir);
    }
    if cfg!(windows) {
        std::env::var("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|_| home().join("AppData").join("Local"))
            .join("IrisEcho")
    } else if cfg!(target_os = "macos") {
        home().join("Library").join("Application Support").join("IrisEcho")
    } else {
        std::env::var("XDG_DATA_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|_| home().join(".local").join("share"))
            .join("irisecho")
    }
}

fn resources(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .resource_dir()
        .map(|d| d.join("resources"))
        .map_err(|e| format!("Could not find the app's resources: {e}"))
}

fn venv_python(venv: &Path) -> PathBuf {
    if cfg!(windows) {
        venv.join("Scripts").join("python.exe")
    } else {
        venv.join("bin").join("python")
    }
}

fn find_wheel(dir: &Path) -> Result<PathBuf, String> {
    fs::read_dir(dir)
        .map_err(|e| format!("Could not read {}: {e}", dir.display()))?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .find(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with("irisecho_core-") && n.ends_with(".whl"))
        })
        .ok_or_else(|| "The installer is missing the IrisEcho core package.".to_string())
}

// --- status ------------------------------------------------------------------

fn set_status(status: &Arc<Mutex<BootStatus>>, stage: &str, message: &str) {
    let mut s = status.lock().unwrap();
    s.stage = stage.into();
    s.message = message.into();
}

fn push_line(status: &Arc<Mutex<BootStatus>>, line: &str) {
    let line = line.trim();
    if line.is_empty() {
        return;
    }
    let mut s = status.lock().unwrap();
    s.lines.push(line.to_string());
    let excess = s.lines.len().saturating_sub(200);
    if excess > 0 {
        s.lines.drain(..excess);
    }
}

// --- processes ---------------------------------------------------------------

fn hide_window(cmd: &mut Command) -> &mut Command {
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd
}

fn uv_command(uv: &Path, data: &Path) -> Command {
    let mut cmd = Command::new(uv);
    cmd.env("UV_CACHE_DIR", data.join("cache").join("uv"))
        .env("UV_PYTHON_INSTALL_DIR", data.join("python"))
        // Never a Python that happens to be on the computer: the environment
        // would break when that one is updated or removed.
        .env("UV_PYTHON_PREFERENCE", "only-managed")
        .env("UV_NO_CONFIG", "1")
        .env("UV_NO_PROGRESS", "1")
        .env_remove("VIRTUAL_ENV");
    hide_window(&mut cmd);
    cmd
}

/// Run a command to completion, feeding its output to the splash screen.
fn run_logged(cmd: &mut Command, status: &Arc<Mutex<BootStatus>>) -> Result<(), String> {
    let mut child = cmd
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Could not start {:?}: {e}", cmd.get_program()))?;
    let stderr = child.stderr.take().unwrap();
    let err_status = status.clone();
    let err_thread = thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            push_line(&err_status, &line);
        }
    });
    for line in BufReader::new(child.stdout.take().unwrap())
        .lines()
        .map_while(Result::ok)
    {
        push_line(status, &line);
    }
    let _ = err_thread.join();
    let code = child.wait().map_err(|e| e.to_string())?;
    if code.success() {
        Ok(())
    } else {
        let tail = status.lock().unwrap().lines.iter().rev().take(6).cloned().collect::<Vec<_>>();
        Err(format!(
            "Setting up IrisEcho failed.\n{}",
            tail.into_iter().rev().collect::<Vec<_>>().join("\n")
        ))
    }
}

/// Create (or refresh) the private environment the core runs in.
fn ensure_runtime(app: &AppHandle, status: &Arc<Mutex<BootStatus>>) -> Result<PathBuf, String> {
    let res = resources(app)?;
    let uv = res.join(if cfg!(windows) { "uv.exe" } else { "uv" });
    let wheel = find_wheel(&res)?;
    let data = data_dir();
    let runtime = data.join("runtime");
    let venv = runtime.join(".venv");
    let python = venv_python(&venv);
    let stamp = runtime.join("installed.txt");
    // Name, size and time: a rebuilt package with the same version still counts as new.
    let meta = fs::metadata(&wheel).map_err(|e| e.to_string())?;
    let modified = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let wanted = format!(
        "{}:{}:{}",
        wheel.file_name().unwrap().to_string_lossy(),
        meta.len(),
        modified
    );

    if python.exists() && fs::read_to_string(&stamp).map(|s| s.trim() == wanted).unwrap_or(false) {
        return Ok(python);
    }

    set_status(status, "installing", "Setting up IrisEcho for the first time. This takes a minute.");
    fs::create_dir_all(&runtime).map_err(|e| e.to_string())?;
    run_logged(
        uv_command(&uv, &data).args(["venv", "--clear", "--python", PYTHON]).arg(&venv),
        status,
    )?;
    run_logged(
        uv_command(&uv, &data)
            .args(["pip", "install", "--python"])
            .arg(&python)
            .arg(&wheel),
        status,
    )?;
    fs::write(&stamp, &wanted).map_err(|e| e.to_string())?;
    Ok(python)
}

fn free_port() -> Result<u16, String> {
    for port in [7788u16, 0] {
        if let Ok(listener) = TcpListener::bind(("127.0.0.1", port)) {
            return listener.local_addr().map(|a| a.port()).map_err(|e| e.to_string());
        }
    }
    Err("No free local port.".into())
}

fn start_core(python: &Path, status: &Arc<Mutex<BootStatus>>) -> Result<(Child, u16), String> {
    set_status(status, "starting", "Starting IrisEcho");
    let port = free_port()?;
    let logs = data_dir().join("logs");
    fs::create_dir_all(&logs).map_err(|e| e.to_string())?;
    let log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(logs.join("core.log"))
        .map_err(|e| e.to_string())?;
    let mut cmd = Command::new(python);
    cmd.args(["-m", "irisecho_core", "serve", "--port", &port.to_string(), "--stdin-watch"])
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped())
        .stdout(Stdio::from(log.try_clone().map_err(|e| e.to_string())?))
        .stderr(Stdio::from(log));
    hide_window(&mut cmd);
    let mut child = cmd.spawn().map_err(|e| format!("Could not start IrisEcho: {e}"))?;

    let deadline = Instant::now() + Duration::from_secs(90);
    while Instant::now() < deadline {
        if let Ok(Some(code)) = child.try_wait() {
            return Err(format!(
                "IrisEcho stopped while starting ({code}). Details are in {}.",
                logs.join("core.log").display()
            ));
        }
        if TcpStream::connect_timeout(&([127, 0, 0, 1], port).into(), Duration::from_millis(200)).is_ok() {
            return Ok((child, port));
        }
        thread::sleep(Duration::from_millis(150));
    }
    let _ = child.kill();
    Err("IrisEcho took too long to start.".into())
}

fn boot(app: AppHandle) {
    thread::spawn(move || {
        let shell = app.state::<Shell>();
        let status = shell.status.clone();
        {
            let mut s = status.lock().unwrap();
            *s = BootStatus { stage: "preparing".into(), message: "Getting ready".into(), lines: vec![] };
        }
        let result = ensure_runtime(&app, &status).and_then(|py| start_core(&py, &status));
        match result {
            Ok((child, port)) => {
                *shell.core.lock().unwrap() = Some(child);
                set_status(&status, "ready", "");
                if let Some(win) = app.get_webview_window("main") {
                    let url = tauri::Url::parse(&format!("http://127.0.0.1:{port}/")).unwrap();
                    let _ = win.navigate(url);
                }
            }
            Err(e) => set_status(&status, "error", &e),
        }
    });
}

fn stop_core(app: &AppHandle) {
    let shell = app.state::<Shell>();
    let child = shell.core.lock().unwrap().take();
    if let Some(mut child) = child {
        drop(child.stdin.take()); // the core exits when this pipe closes
        let deadline = Instant::now() + Duration::from_secs(8);
        while Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                return;
            }
            thread::sleep(Duration::from_millis(100));
        }
        let _ = child.kill();
    }
}

// --- commands for the web UI ---------------------------------------------------

#[tauri::command]
fn boot_status(shell: tauri::State<'_, Shell>) -> BootStatus {
    shell.status.lock().unwrap().clone()
}

#[tauri::command]
fn retry_boot(app: AppHandle) {
    stop_core(&app);
    boot(app);
}

#[tauri::command]
async fn pick_folder(app: AppHandle) -> Option<String> {
    app.dialog()
        .file()
        .blocking_pick_folder()
        .and_then(|p| p.into_path().ok())
        .map(|p| p.display().to_string())
}

#[tauri::command]
fn open_external(app: AppHandle, url: String) -> Result<(), String> {
    if !(url.starts_with("https://") || url.starts_with("http://")) {
        return Err("only web links can be opened".into());
    }
    app.opener().open_url(url, None::<&str>).map_err(|e| e.to_string())
}

#[tauri::command]
fn open_logs(app: AppHandle) -> Result<(), String> {
    let logs = data_dir().join("logs");
    let _ = fs::create_dir_all(&logs);
    app.opener()
        .open_path(logs.display().to_string(), None::<&str>)
        .map_err(|e| e.to_string())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Shell::default())
        .invoke_handler(tauri::generate_handler![
            boot_status,
            retry_boot,
            pick_folder,
            open_external,
            open_logs
        ])
        .setup(|app| {
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("IrisEcho")
                .inner_size(1440.0, 920.0)
                .min_inner_size(1024.0, 680.0)
                .background_color(tauri::window::Color(14, 11, 22, 255))
                .build()?;
            boot(app.handle().clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("IrisEcho could not start its window")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                stop_core(app);
            }
        });
}
