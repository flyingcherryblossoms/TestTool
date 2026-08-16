# Repository Guidelines
## Project Overview

TestTool is a cross-platform (Windows/Linux/macOS) desktop network testing tool built with Python + PySide6 (Qt). It provides:
- Batch TCP connectivity checks (up to 200 concurrent threads), IP/CIDR/port-range expansion, port scanning
- TCP/WebSocket/HTTP protocol testing: clients, mock servers, message presets, load/stress testing (token-bucket QPS limiting)
- Collection management with CSV/Excel/JSON import/export, test history, CSP hex-dump parser

## Architecture & Data Flow

Layered structure, all under `src/`:
- **`main.py`** — entry point; `--db <path>` selects DB location. Project root `testtool.db` in source mode, exe-adjacent when PyInstaller-frozen.
- **Data layer** — `src/database.py`: single `Database` class wrapping SQLite (WAL mode, foreign keys on). All queries return dataclasses (`Collection`, `Target`, `TestSession`, `TestResult`, `ProtocolCollection`, `ProtocolMessage`, ...). Fresh connection per operation via `_connect()`; no long-lived shared connection.
- **Engines** — `src/scanner.py` (TCP connect engine + IP/port expansion), `src/protocol.py` (TCP/WS client & server engines with length-prefixed framing, ported from a Java "TestUtil"). Engine `start()` methods are **blocking by design** and run inside QThreads.
- **Import/export** — `src/csv_handler.py`, `src/excel_handler.py` (openpyxl/xlrd), `src/json_handler.py`.
- **UI layer** — `src/ui/`: PySide6 panels/dialogs/workers.

Data flow: UI panel → QThread Worker → engine (blocking) → Qt `Signal`s back to UI → `Database` persists results; import/export handlers read/write through the same dataclasses.
## Key Directories

- `main.py` — entry point, icon loading, Qt log filter
- `src/` — core modules (database, scanner, protocol, handlers)
- `src/ui/` — all PySide6 widgets: `main_window.py`, `connectivity_panel.py`, `protocol_panel.py`, `protocol_components.py` (ClientPanelBase/ServerPanelBase), `protocol_workers.py` (QThread workers incl. `StressTestWorker`), `http_client.py`, `target_panel.py`, `test_panel.py`, `result_panel.py`, `collection_sidebar.py`, `port_scan_dialog.py`, `csp_parser_dialog.py`, `format_text.py` (rich-text message editor), `shortcuts.py` + `shortcut_settings_dialog.py` (configurable hotkeys), `clipboard.py` (in-app copy/paste)
- `resources/` — app icons (`icon.ico`, `icon.png`)
- `.github/workflows/build.yml` — multi-platform PyInstaller build matrix

## Development Commands

```bash
# Run from source (Python 3.8+)
pip install PySide6 openpyxl xlrd websocket-client websockets requests
python main.py                          # GUI
python main.py --db /path/to/testtool.db   # custom DB location

# Package (Windows x64)
pip install pyinstaller
pyinstaller --onefile --windowed --name TestTool \
    --icon=resources/icon.ico \
    --add-data "resources/icon.ico;resources" \
    --clean --noconfirm main.py
```

Release builds are automated: push a `v*` git tag → GitHub Actions (`.github/workflows/build.yml`) builds Windows x64, Linux x64/ARM64 (+ Python 3.8 compat variants), macOS ARM64 and publishes a Release. No local build scripts beyond the PyInstaller invocations above.
## Code Conventions & Common Patterns

- **Language**: Python 3.8-compatible (CI builds the compat targets on 3.8; keep `from __future__ import annotations`). Chinese docstrings/comments throughout — follow suit for new code.
- **Threading pattern (most important)**: all blocking work runs in `QThread` subclasses that define Qt `Signal`s (`progress`, `finished_all`, `finished(bool, str)`) and call a blocking engine method inside `run()`; the main thread stops them via a `stop()` that sets a flag/closes sockets. Workers live in `src/ui/protocol_workers.py`, `src/scanner.py` (`ScannerWorker`), `src/ui/http_client.py`, `src/ui/target_panel.py` (`ImportWorker`). Never do socket I/O on the GUI thread.
- **Cancellation**: engines poll a `threading.Event` (e.g. `cancel_event`) or accept-timeout loop (`ACCEPT_TIMEOUT = 1.0` in `protocol.py`); mirror this when adding new long-running work.
- **Concurrency for scans**: `ThreadPoolExecutor` with `max_workers` (default 30, UI max 200) + per-target progress callback; see `scan_targets_sync()` in `src/scanner.py`.
- **Database access**: one `Database(db_path)` instance per app run; methods open a fresh connection per call (`_connect()`, WAL + foreign keys), use `sqlite3.Row` → dataclass. Batch writes for bulk results. Do not hold connections across threads.
- **Framing protocol**: length-prefixed messages — `pack_message()`/`compute_length_header()` in `src/protocol.py` (`head_len=0` means raw body). Keep request/response log format `[yyyy-MM-dd HH:mm:ss][role][ip:port]: message`.
- **Error handling**: engines raise `ConnectionError`/plain exceptions with Chinese messages; UI catches at worker boundaries and surfaces via signals/status bar. No global exception framework.
- **State/config persistence**: everything (targets, presets, server configs, hotkey bindings, stress params) lives in SQLite via `database.py`; UI panels reload from DB after mutations rather than keeping parallel state.
## Important Files

- `main.py` — entry point, CLI parsing, icon fallback, Qt message filter
- `src/database.py` (~1500 lines) — schema (`SCHEMA_SQL`) + all CRUD; read first when touching any persistence
- `src/protocol.py` — TCP/WS framing + client/server engines (blocking `start()`)
- `src/scanner.py` — `ScanTarget`/`ScanResult`, IP/port expansion, `scan_targets_sync`, `ScannerWorker`
- `src/ui/protocol_components.py` & `src/ui/protocol_panel.py` — largest UI files; shared Client/Server panel bases
- `.github/workflows/build.yml` — canonical dependency list + PyInstaller flags per platform
- `testtool.db` — local SQLite (gitignored via `*.db`)

## Runtime/Tooling Preferences

- **Runtime**: Python ≥ 3.8 (`.python-version` pins 3.8; CI uses 3.12 for normal builds, 3.8 only for the Linux compat jobs). No Node/Bun involved.
- **Package manager**: plain `pip` is canonical (CI installs deps with pip). A `uv.lock` + `pyproject.toml` exist at root, so `uv sync` also works locally — but keep `pyproject.toml` dependencies in sync with the CI pip list (`PySide6 openpyxl xlrd websocket-client websockets requests` [+ `Pillow`, `pyinstaller` for packaging]).
- **No linter/formatter configured** (no ruff/black/eslint config). Match surrounding style manually: snake_case files/functions, PascalCase classes, 4-space indent.

## Testing & QA

- **No automated test suite exists** — no `tests/`, pytest, or CI test job (`test_panel.py` is a UI panel, not tests). Do not assume `pytest` will find anything.
- Verify changes by running the app: `python main.py`. For protocol work, start a mock server from the UI and exercise it with the client panel.
- When adding tests, use pytest with a temp SQLite file (point `Database` at a throwaway path); keep them independent of the real `testtool.db`.
