# Platform setup and boundaries

The web server and process launcher use Python 3.11+ standard library APIs.
The terminal board and live Firstmate producer still require POSIX process, shell and filesystem capabilities.
The web and terminal share `scripts/flow_tui.py`, including owner resolution, event provenance, calls, progress and retained deliveries.

| Environment | Web server/control | Live producer / terminal | Validation for this change |
| --- | --- | --- | --- |
| Linux | Native Python | Bash, compatible Firstmate; Herdr for pane activity/actions | Executed with isolated fixtures |
| macOS | Native Python entrypoints | POSIX backend, Bash, compatible Firstmate/Herdr | Not executed on macOS here |
| Windows native | Python launcher and empty local UI | Shell calls and unsafe report fallbacks are refused; interactive TUI unavailable | Not executed on Windows here |
| Windows WSL2 | Linux Python inside WSL | Keep Firstmate, Deck and tools together inside WSL | Not executed on WSL2 here |

CI includes native launcher tests for Linux/macOS/Windows, but adding a job is not evidence that it passed.
No claim of native Firstmate or Herdr support on Windows follows from a working empty web UI.
For Windows with live data, use WSL2, run both projects there, and open `http://127.0.0.1:8787/` from the Windows browser.
Do not combine Linux home paths with Windows executables or expect Git Bash to supply POSIX locking, process or descriptor-relative filesystem semantics.

## Checkout and foreground startup

Use your chosen reviewed public fork checkout.
No dependency installation is needed for serving the web UI.
From its root on Linux/macOS/WSL run `python3 web/serve.py run`; on Windows run `py -3 web/serve.py run`.
Ctrl+C stops it.
The public bind remains `127.0.0.1`; external access is optional private proxy configuration documented in [web setup](../web/README.md).

For a full backend, configure `FM_HOME` or a labelled `FM_FLOW_HOMES_JSON` map to a private Firstmate home.
Set `FM_FLOW_HOMES_ONLY=1` to restrict discovery to the explicit labelled list.
`FM_FLOW_HOMES_JSON={}` with homes-only intentionally selects no homes for an empty setup.
Use `homes.conf` or JSON for paths containing spaces; the legacy whitespace-separated `FM_FLOW_HOMES` syntax remains available.
`HERDR_PLUGIN_CONFIG_DIR`, `HERDR_PLUGIN_STATE_DIR`, `HERDR_BIN_PATH`, `CD_WEB_DATA` and `CD_WEB_RUNTIME` keep all state and integrations configurable.
On POSIX the prior `~/.config/captains-deck-web` and `~/.local/state/captains-deck-web` defaults remain, respecting XDG overrides.
On native Windows the launcher uses the user's `LOCALAPPDATA/captains-deck-web` directory.

## Firstmate code and home contract

`FM_ROOT_OVERRIDE` selects a separate Firstmate code root.
Deck first uses an existing `<home>/bin/<endpoint>` and otherwise resolves `<FM_ROOT_OVERRIDE>/bin/<endpoint>`.
It always passes the owning `FM_HOME` to the subprocess.
This retains configured home-local wrappers and supports producer installations with separate private data and public code.
The helper root is trusted local configuration, never a request parameter.

Required producer surfaces are unchanged:

- `fm-bearings-snapshot.sh --json --fields paths` supplies the board, progress, source times and optional supervisor declaration.
- `fm-captain-hold.sh capabilities --json` must advertise `fm-captain-hold.capabilities.v1`, `guarded_input: single-row`, and `expected_identity` for `answers` and `reconcile-requests` before web submission is available.
- `open TASK --identity` supplies the hold lifecycle identity; the guarded intake compares `--expected-identity` under the owner's existing lock.
- `fm-delivery-history.sh --json` supplies `fm-delivery-history.v1`, the exact owner home and retained records; older local homes retain the existing canonical tasks-axi/Node fallback.
- `fm-delivery-explain.sh --task TASK --request-id ID --json` and `--status` retain stable deduplication and recorded ACK/reply semantics.

Missing capabilities remain visible or fail closed.
Answers, reconcile requests and explanations never become direct task-file writes.
Native Windows lacks the report reader's required descriptor-relative `O_NOFOLLOW` support; Deck reports that limitation rather than using a vulnerable resolve-then-open fallback.
The web bind, Host/Origin checks, optional proxy identity, session and CSRF protections apply on every platform.

## Persistence and upgrades

`web/serve.py start|status|stop --config /absolute/private/deck.json` controls only the managed instance whose private token belongs to that config's runtime directory.
`managed` stays foreground for a user service manager; `run` stays foreground without management records.
Python is selected by the invoked interpreter, and background startup reuses `sys.executable`.
No launcher searches for or kills processes by name/PID.
Existing POSIX cron entries using `web/run-managed.sh` retain their environment, log, lock and receipt locations.
The wrapper still cooperates with a running legacy flock holder and will not duplicate it.

Optional user service setup is an operator action: use an absolute interpreter path, `web/serve.py managed`, and an absolute private config path as the command.
On macOS use a LaunchAgent; on Linux use a user systemd service or existing scheduler; on Windows use a per-user Scheduled Task for the native empty UI, or the Linux service manager inside WSL for the full stack.
Use the OS manager to control an instance it started; do not install two different restart mechanisms for the same runtime directory.
This repository never installs services or changes an existing proxy automatically.

Keep receipts, owner data, configuration, runtime tokens and logs outside the public checkout.
Preserve them when replacing code.
Test a new export using a different temporary runtime directory and port before upgrading a live deployment.

## Terminal controls and remote homes

| Terminal shortcut | Action |
| --- | --- |
| `m` | Open Message Firstmate; Enter sends and Esc closes. |

Remote second mates use `fm-on.sh` for terminal snapshots; remote web capabilities depend on the backend.
