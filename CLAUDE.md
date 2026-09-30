# IrisEcho: notes for contributors and coding agents

## Layout

- `core/src/irisecho_core/`: the Python core (FastAPI server, job queue, GPU
  lease, registry, downloads, CLI). `engines/` holds one package per engine;
  each worker script runs in that engine's own uv environment and imports
  only `engines/protocol/*` (stdlib + numpy). `AGENTS.md` in the package is
  the guide shipped to people's scripts and agents for *using* IrisEcho; keep
  it true when the CLI or the API changes.
- `core/src/irisecho_core/registry/models.yaml` + `lock.json`: every model
  file, pinned. After editing the YAML run `uv run python scripts/lock_registry.py`
  and `uv run python scripts/gen_models_md.py`.
- `ui/`: Svelte 5 interface; `npm run build` writes into the core package.
- `desktop/`: Tauri 2 shell; `uv run python scripts/build_desktop.py` builds
  the installer for the current platform. A new shell command must be named in
  `build.rs` and granted in `capabilities/`: the app's page is a remote origin
  to Tauri and may call only what `app-page.json` lists. The built
  `target/release/irisecho.exe` runs without installing; give it
  `IRISECHO_HOME` and `WEBVIEW2_USER_DATA_FOLDER` of its own.

## Checks before every commit

```sh
uv run ruff format && uv run ruff check && uv run pytest
(cd ui && npm run check)
uv run python scripts/check_comfy_graphs.py   # after changing ComfyUI graphs (needs the engine installed)
```

Commits are signed off (`git commit -s`) with a GitHub noreply address; the
hooks from `python scripts/dev_setup.py` enforce this and run the identity scan.

## Rules that matter

- Nothing identifying goes in the repo: no personal paths, hostnames, LAN
  addresses, real emails, or image metadata. The identity scan blocks them.
- Every new dependency or model gets its license checked (code must be
  AGPL-compatible) and recorded in THIRD_PARTY_NOTICES.md or the registry.
- Linked model folders are read-only. An engine that writes into its model
  folder gets `no_link: true` in the registry.
- Never accept a model license or use a Hugging Face token on someone's behalf.
- Use `IRISECHO_HOME=<folder>` for test data so a real install is untouched.
- The desktop shell runs the core as `serve --stdin-watch` with stdin a pipe.
  Try anything that starts a process that way, not only with `uv run irisecho`;
  always pass `stdin=DEVNULL` (or a pipe of your own) when starting one.
- Writing Python or TypeScript through shell heredocs can turn `\n`, `\b`,
  `\0` escapes into raw control characters; edit files directly instead.
  `tests/test_source_hygiene.py` catches it.
