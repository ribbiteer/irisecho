// SPDX-License-Identifier: AGPL-3.0-or-later
fn main() {
    // Naming the shell's commands here makes each one a permission
    // (`allow-pick-folder`, ...) that a capability has to grant. The app's own
    // page is served from http://127.0.0.1, which Tauri counts as a remote
    // origin: it may call only what capabilities/app-page.json lists.
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "boot_status",
            "retry_boot",
            "pick_folder",
            "open_external",
            "open_logs",
        ]),
    ))
    .expect("failed to run tauri-build");
}
