# Captain’s Deck web

A private, responsive live view of Firstmate, with guarded Captain’s Call answers and retained delivery history.
Python 3.11+ is the only runtime dependency.
The bridge imports `scripts/flow_tui.py`, so the terminal and web use the same task, flow, delivery, dependency and event-time contracts.
There is no second backlog or supervisor.
The established terminal interactions are preserved.

On a phone, four named state buttons select one full-width column.
Desktop has four columns; tablets have two.
Integration remains a phase of Underway.
Cards lead with a human summary, the registered executor, dependencies and any concrete recorded next action.
The source home coordinates work; it is not the executor.
Absent summaries, resume conditions and next actions remain explicit information gaps for Firstmate to fill.
Each card states what is happening now: an agent working, a validation run, or what it waits for (another task, a decision, an approval, a date).
The header and the Underway heading count only agents whose own pane Firstmate reports busy; a working verdict from a status log or any other source is not counted, and Underway still lists every open task.
Up next separates work ready to start from work blocked by another task, held, or deferred to a date.
Validation job progress does not establish live native worker activity.
Agent presence comes from Firstmate's endpoint probe; hold and pause end dates appear only when Firstmate exports them as fields, never from reason text.
Original reports, supervisor declarations and raw producer fields share one optional technical disclosure.
Producers can supply `flow.presentation.summary`, `waiting_for` and `next_action` as concise authored text; absent fields remain gaps and known generic phase instructions do not become concrete actions.
Deliveries open a readable summary with a separate Report view and optional technical details.
History searches the retained current/archive export, including deliveries beyond the recent cap.
Relative event time is primary and updates while the view stays open without replacing focused controls or announcing a timer.
The date disclosure opens with touch or keyboard and shows exact browser-local time.
Date-only events keep their calendar date and never acquire an hour; unknown times and future clock uncertainty stay explicit.
UTC and original provenance remain in technical details.

## Run locally

Python 3.11+ and its standard library are enough to start the web server.
From the checkout, run:

```sh
python3 web/serve.py run
```

In Windows PowerShell, use `py -3 web/serve.py run`.
Open `http://127.0.0.1:8787/`; Ctrl+C stops the foreground server.
An unconfigured server shows an empty board and the missing-home diagnostic; `/health` stays 503 until a valid configured source is read.
No Tailscale, Herdr process, build step or package installation is needed for the empty web UI.
A live board needs a compatible Firstmate producer in the same OS environment.
For the full stack on Windows, run Firstmate and Deck inside WSL2 and open the loopback URL from Windows.
Native Windows serving/control is implemented, but live Bash producer calls and safe report reads require POSIX/WSL; they do not silently fall back to weaker filesystem checks.
See [platform setup and validation limits](../docs/portability.md).

For an existing owner on Linux/macOS/WSL:

```sh
FM_FLOW_HOMES='firstmate=/absolute/firstmate-home' \
FM_FLOW_HOMES_ONLY=1 python3 web/serve.py run
```

For paths containing spaces, use `homes.conf` (one `label=path` per line) or `FM_FLOW_HOMES_JSON` (a JSON object mapping labels to paths).
Explicit configured homes remain visible even before they contain tasks.
`FM_ROOT_OVERRIDE` optionally selects a separate Firstmate code checkout; an existing owner `bin/` endpoint takes precedence for compatibility.
Every child process still receives the owning `FM_HOME`.

| Environment | Default | Purpose |
| --- | --- | --- |
| `CD_WEB_LANGUAGE` | `en` | Explicit per-installation UI language: `en`, `es`, or a regional variant |
| `CD_WEB_PORT` | `8787` | Listener port, 1024–65535 |
| `CD_WEB_BIND` | `127.0.0.1` | Other bind addresses are rejected |
| `CD_WEB_ORIGIN` | empty | Exact optional private HTTPS origin, without path |
| `CD_WEB_USERS` | empty | Comma-separated authenticated proxy identities, required with an external origin |
| `CD_WEB_HERDR_ORIGIN` | empty | Optional supported Herdr Web HTTPS origin |
| `CD_WEB_DATA` | user config directory | Private deduplication receipts; no new task queue |
| `CD_WEB_RUNTIME` | user state directory | Managed instance control record, lifetime lock and log |
| `FM_ROOT_OVERRIDE` | empty | Optional Firstmate code root fallback |
| `FM_BASH` | `bash` | POSIX interpreter for explicit shell endpoint calls |
| `FM_FLOW_HOMES_JSON` | unset | JSON label/path mapping; `{}` with homes-only disables automatic discovery |
| `FM_FLOW_HOMES`, `FM_FLOW_HOMES_ONLY` | existing Deck discovery | Pin the owner scope for a service |

The server collects All and filters each request independently.
Two browsers selecting different crews cannot switch one another's board.
Source-read times remain separate from collector repaint and task event times.
A failed source retains its last successful read; a reconnect never submits a draft.

## Private configuration and process control

Keep deployment choices outside the checkout in a private JSON file containing the environment keys above, with string values.
`serve.py --config` loads that explicit file over inherited environment values.
For example, an empty local-only board needs just:

```json
{
  "CD_WEB_PORT": "8787",
  "FM_FLOW_HOMES_JSON": "{}",
  "FM_FLOW_HOMES_ONLY": "1"
}
```

```sh
python3 web/serve.py start --config /absolute/private/deck.json
python3 web/serve.py status --config /absolute/private/deck.json
python3 web/serve.py stop --config /absolute/private/deck.json
```

Use the same config for all commands.
`start` launches one managed instance; `managed` holds it in the foreground for a service manager.
Control uses a private instance token on a separate loopback socket, never a PID kill or a public HTTP action.
An old or unrelated PID cannot be stopped by this launcher.
The private runtime directory must be readable/writable only by its owner; on Windows use a user-owned directory with appropriate ACLs.
Do not place it on a shared/network filesystem.
The POSIX lock is compatible with the previous `run-managed.sh` flock lock, so existing cron entries can retain their paths and environment.
A legacy instance without a control record remains under its existing process manager; the new control commands do not adopt or stop it.
Keep answer receipts across updates and restarts.

For login persistence, configure your OS user service manager to run `serve.py managed --config ...` with absolute Python, script and config paths.
Linux systemd user services, macOS LaunchAgents and Windows Task Scheduler can use that same foreground entrypoint.
Nothing installs, enables or changes a service automatically.

## Language and appearance

English is the deterministic public default, including on Spanish-language systems and browsers.
Set `CD_WEB_LANGUAGE` in the existing private JSON configuration to select this installation’s language:

```json
{
  "CD_WEB_LANGUAGE": "es-MX",
  "CD_WEB_PORT": "8787",
  "FM_FLOW_HOMES_JSON": "{}",
  "FM_FLOW_HOMES_ONLY": "1"
}
```

```sh
python3 web/serve.py run --config /absolute/private/deck.json
```

For a temporary foreground preference on Linux, macOS or WSL:

```sh
CD_WEB_LANGUAGE=es python3 web/serve.py run
```

In PowerShell, use `$env:CD_WEB_LANGUAGE = 'es'; py -3 web/serve.py run`.
An explicit `--config` value overrides the inherited environment; an absent key uses the environment, then `en`.
The JSON file persists across launcher restarts and upgrades without editing application source.
Restart that Deck server with the same configuration and reload its browsers after changing the value.
Use the existing service manager for a service-managed installation.
No Herdr restart is needed.

Supported values are `en` and `es`, optionally followed by a two-letter or three-digit region: for example `en-GB`, `en-US`, `es-ES`, `es-MX` or `es-419`.
Case and surrounding whitespace are normalized; underscores are accepted as region separators.
Regional variants share their language catalog and use the regional locale for dates.
Unknown languages, malformed values, extensions and `auto` fall back to `en`.
Missing translation keys fall back to English.
There is no automatic browser, OS or agent-conversation language detection.
An agent selects the desired language by reading or updating this explicit installation preference.
All browsers of that server see the same language; independent servers with separate configurations remain independent.

Only presentation changes: task titles, authored questions/options, reports, answers, URLs and protocol/state identifiers retain their content.
Exact times use the selected locale and the browser’s timezone; date-only events keep their calendar date without inventing a time or shifting across timezones.
Provenance retains the original source and timestamp values.

The header’s **Theme** selector offers **System**, **Light** and **Dark**.
System is the initial choice and follows the operating system’s appearance, including changes while the page is open.
An explicit choice persists in that browser’s local storage for the Deck origin; another browser or origin has its own choice.
When storage is disabled the choice works for the current page.
Theme is independent of the server language and does not write tasks or configuration.

### Adding translations

`web/locales/en.json` and `es.json` are plain-text catalogs shared by the Python bridge and browser.
English source messages are stable keys; preserve named placeholders such as `{count}` and `{date}`.
To add a language, copy the English catalog, translate its values, and extend the language pattern in `normalize_language` in `web/localization.py` (currently the bounded `en|es` match); without that change the new code normalizes to `en`.
No translation framework or service is needed.
The shared TUI helpers accept an optional presentation callback; their default terminal wording remains unchanged.
Never run a translator over authored task content or decision payloads.
Use `t(...)` for new UI chrome and `data-i18n`, `data-i18n-aria-label` or `data-i18n-placeholder` for static document text.
Catalog values enter the page through `textContent` and safe attribute setters.

## Optional private HTTPS proxy

Keep the listener on `127.0.0.1` and use a dedicated private HTTPS origin with no URL prefix.
Set `CD_WEB_ORIGIN` to that exact origin and `CD_WEB_USERS` to the intended authenticated identities.
A trusted local proxy such as Tailscale Serve must supply `Tailscale-User-Login` after authenticating the caller and must remove any caller-supplied version of that header.
Missing identity or configuration fails closed.
Never expose this listener through a public tunnel or untrusted proxy.
No separate dashboard is required or included.

Local access requires the exact loopback Host.
Remote access requires the configured Host and allowed identity.
Every write requires exact Origin, JSON, a session cookie and a session-bound CSRF header.
Cross-site API reads, embedded frames and writes remain rejected.
A user-activated cross-site GET navigation to `/` or `/index.html` may open the initial document, still subject to Host and identity checks.
Existing private origin/users/Herdr-origin environment configuration remains supported.

## Actions and receipts

The current Firstmate owner must expose `fm-captain-hold.sh capabilities --json` with schema `fm-captain-hold.capabilities.v1`, `guarded_input:single-row` and `expected_identity` including `answers` and `reconcile-requests`.
`open TASK --identity` supplies the lifecycle (`hold-set#resolution-count`).
The bridge sends that exact identity via `--expected-identity` on the public keyed-answer or reconcile-request intake.
Firstmate compares it under the existing owner task-control lock.
Without this capability, the question remains readable and submission fails closed.
The bridge also binds the rendered question/options to an opaque occurrence token and revalidates home, task, question and state.
One durable receipt per occurrence prevents double submission across browsers and restarts.
Interrupted calls remain explicitly uncertain; a retry reads that receipt rather than automatically invoking the intake again.
Conflicts preserve the session draft and require reviewing the current question.
The keyed intake currently accepts a single-line answer of up to 512 bytes; over-limit answers are rejected before submission, never silently truncated.

Intake acceptance confirms a saved answer or review request.
Notice, ACK and consumption are separate fields and remain unconfirmed without owner evidence.
No receipt claims the agent resumed.
Reconcile is a request to recheck, never an answer or a release.
Explanations use `fm-delivery-explain.sh --task … --request-id … --json`, with `--status` for reads.
The existing stable home/task request ID owns deduplication; actual replies and ACKs are displayed as recorded.
Reports and user content only reach DOM `textContent`; report links are text, never commands or authority.
Report reads reuse the model’s task scope, size bounds and symlink/traversal protections.

The prior experimental Web Push surface is retired from this server.
It creates no keys/subscriptions, sends no alerts, and unregisters its old worker on an existing Deck origin.
Legacy push utilities are excluded from the public source export.

## Endpoints

All routes are on the dedicated origin and require the trust checks above.
Identifiers are server-issued opaque owner/card IDs; clients cannot supply filesystem roots.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/locale` | Instance language, regional locale and plain-text UI catalog |
| GET | `/health` | Readiness: 503 while the first snapshot is loading or unavailable, 200 after a valid snapshot |
| GET | `/api/board?home=ID` | Four states, sources, owner declarations and session CSRF |
| GET | `/api/ticket/KEY` | Full task context or current question/options |
| POST | `/api/ticket/KEY/answer` | Explicit answer: value, note, occurrence, request_id |
| GET | `/api/receipts` | This actor’s latest ten durable answer receipts |
| GET | `/api/history?home=ID&q=TEXT` | Retained history search, up to 1000 results with omission count |
| GET | `/api/delivery/HOME/TASK` | Readable summary, report and details |
| GET | `/api/delivery/HOME/TASK/explanation` | Actual explanation receipt, ACK and reply |
| POST | `/api/delivery/HOME/TASK/explanation` | Explicit request through the existing owner intake |

The listener can answer requests before discovery, Herdr and bearings finish.
During this startup interval the board reports `loading=true`; empty homes or history do not mean the owner has no retained work.
Health reports `ok=false`, `collector_at=0` and HTTP 503 until the first valid snapshot arrives.
Its `loading` and `error` fields distinguish a pending read from a reported collector failure.
Health confirms collector readiness; source timestamps and errors on the board describe freshness separately.

## Validation

```sh
python3 -m unittest discover -s /absolute/captains-deck/web/tests -p 'test_*.py' -v
node /absolute/captains-deck/web/tests/localization.test.cjs
node /absolute/captains-deck/web/tests/board_dom.test.cjs
```

The browser-helper tests run under Node.js 22, the version CI pins; production still requires only Python.
The public CI runs the launcher and language configuration checks on Linux, macOS and Windows.
Tests exercise the real HTTP boundary and temporary owner subprocesses, including two-browser races, restart deduplication, identity/origin/CSRF rejection, stale questions, long answers, synthetic archive history and report symlinks/traversal.
`web/tests/preview.py /ABSOLUTE/TASK_PROOF_DIR` starts a clearly isolated fixture server on loopback 8788 for browser checks.
Production has no fixture or arbitrary data-file CLI flag.
Viewport emulation tests layout and interaction; it does not constitute physical Firefox Android acceptance.

## Stable notification links

Use `/?home=HOME_ID&task=TASK_ID&view=call` or `view=delivery`.
For a local owner, `HOME_ID` is `sha256(UTF8(realpath(FM_HOME))).hexdigest()[:32]`.
The task is the exact owning-home slug, URL encoded.
The route contains no absolute path, pane/session identity or transient ticket key.
It resolves only configured/discovered homes and never chooses a same-named task in another home.
Opening a link is read-only.
Optional `item=hold:N` binds a call notification to its original canonical hold occurrence.
A replaced occurrence opens an explicit old-notification context, with separate navigation to the current question.
`resolved TASK N` confirms a resolved original call where the owner retains that evidence.
Missing or unverifiable historical evidence stays explicit; it never becomes a new answer form for an old notice.
The current delivery history cannot correlate arbitrary notification item/artifact IDs, so a delivery link with such an item labels that correlation as unverified while showing the retained summary for the exact home/task.
`GET /api/resolve?home=…&task=…&view=…&item=…` is the read-only entry resolver.
