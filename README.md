# Captain's Deck

**See the whole crew. Unblock what matters. Let Firstmate ship the rest.**

Captain's Deck is a read-only **[Firstmate flow](https://github.com/kunchenguid/firstmate)** kanban plugin for [Herdr](https://herdr.dev).
It is the captain's view of agent orchestration: small surface area, like [Pi](https://pi.dev/)—one board that does one job well instead of another dashboard to babysit.

The optional [private web view](web/README.md) brings the same four states to phones and desktop browsers, with retained delivery history and guarded Captain's Call answers.
It runs locally with Python's standard library and can sit behind private Tailscale Serve.

The local [web kanban](web/README.md) defaults to English, supports an explicit per-installation Spanish preference, and offers system, light and dark appearance.

## Web quick start

This customized fork includes the full terminal board and a responsive local web kanban.
Run `python3 web/serve.py run` (`py -3 web/serve.py run` in Windows PowerShell), then open `http://127.0.0.1:8787/`.
The empty UI needs only Python 3.11+; a live Firstmate backend requires POSIX, including WSL2 on Windows.
See [web setup](web/README.md), [platform capabilities](docs/portability.md), and [safe public updates](docs/public-export.md).
Private deployment configuration and receipts stay outside this checkout.
Upstream attribution and the MIT license are preserved.

## TUI quick start

Install and enable the plugin, then open the board:

```bash
herdr plugin install cbriseno/captains-deck
herdr plugin enable herdr-firstmate-flow
herdr plugin action invoke open-captain-deck --plugin herdr-firstmate-flow
```

The last command opens the board in its own `captain's deck` workspace.
The launcher creates that workspace when missing, then reuses it despite case or spacing changes or a leading icon.
For the floating overlay, invoke `open-flow` instead:

```bash
herdr plugin action invoke open-flow --plugin herdr-firstmate-flow
```

Press `q` in the board pane to exit.

Card ages refresh while the board stays open.
When Firstmate exports optional per-row `card_time`, the Deck shows its validated column event (`Filed`/`Queued` or a current hold's `Asked` in Charted Next; `Asked`, `Updated`, `Ready`/`Done report`, or `Delivered` elsewhere) with the producer's exact source in details.
Date and second precision must match the value; instants require an explicit timezone, and events for another column are rejected.
Missing, null or invalid exports retain the following honest fallback; invalid exports also explain their rejection in details.
Details retain the exported value and supplied attempt context; an unknown attempt boundary and a context-only current attempt do not become an event identity.
Cards pair local date/time with relative age when an exact instant is available; the full timestamp, timezone and event provenance remain in details.
Date-only records explicitly lack a recorded hour.
Small windows show the selected column at full width; left/right changes columns.
Compact `?` means unknown, `!` means unavailable, and `fut` marks a future clock value.
`Filed` shows the recorded task filing date; `Updated` uses a meaningful task declaration, and `Done report` shows the worker's completion declaration while awaiting merge.
These do not imply a delivery, a queue-entry time or a review request.
`Asked` and `Delivered` stay unknown when their event time is not exported; opening a delivery can show its retained completion date.
Press `d` for the timestamp's source and exact date/time, including the terminal's local UTC offset and UTC; date-only records never acquire an invented time.

The plugin ID is `herdr-firstmate-flow`, not the repository name, because the
project started under that name.

Bind the board to a key in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+shift+f"
type = "shell"
command = "herdr plugin action invoke open-captain-deck --plugin herdr-firstmate-flow"
description = "open captain's deck (full-pane flow board)"
```

Press `prefix+shift+f` from any Herdr pane. Use the key you want.

Both launchers only touch panes this plugin opened. The launcher reads the
recorded pane id, or it verifies the pane's foreground process.
A user pane that merely shares the `Flow` label is never touched.

Local development:

```bash
herdr plugin link /path/to/herdr-firstmate-flow
```

Check the board without a TTY (run this from a checkout of this repository):

```bash
scripts/kanban-view.sh --homes        # list discovered homes
scripts/kanban-view.sh --once         # one plain-text frame, all homes
scripts/kanban-view.sh --once --home 2ndmate-demo
```

Use `scripts/kanban-view.sh --once --home <name>` with a label from `--homes` to
check one home.

Herdr's plugin marketplace indexes public GitHub repositories tagged with the
`herdr-plugin` topic: <https://herdr.dev/plugins/>.
The install command above selects this fork directly.

Source: <https://github.com/cbriseno/captains-deck>

Upstream: [deimantasnork/captains-deck](https://github.com/deimantasnork/captains-deck).
This fork retains the upstream authorship and [MIT license](LICENSE).

## Why Captain's Deck

Parallel coding agents only feel like a crew when nothing important stalls in a forgotten tab.
[Firstmate](https://github.com/kunchenguid/firstmate) runs that crew for you: isolated worktrees per task, supervision until the work is actually finished, and a clean handoff when the session is done.

Instead of repeatedly checking agents, opening pull requests, watching CI, rebasing branches, resolving merge-flow interruptions, and rerunning validation, Firstmate keeps the delivery loop moving for you.

### Traditional workflow vs Firstmate

| Traditional AI development | Firstmate + Captain's Deck |
| --- | --- |
| Create task | Create task |
| Start agent | Agent starts in an isolated worktree |
| Check progress manually | Firstmate supervises progress |
| Check whether work finished | Completion is tracked automatically |
| Commit + push | Handled as part of the workflow |
| Open PR | PR prepared automatically |
| Watch CI / validation | Validation pipeline is monitored |
| Update / rebase branch | Ship branch stays aligned with `main` |
| Resolve merge conflicts | Firstmate works through conflicts |
| Re-run validation | Configured checks run as part of the flow |
| Review + merge | Can land automatically when policy allows |
| Repeat for every agent | Crew keeps moving in parallel |
| **You operate the workflow** | **You handle the decisions that need you** |

```text
Traditional

Task → Agent → Check → Push → PR → CI → Fix → Rebase → Test → Merge
         ↑                         ↓
         └──── human keeps coming back ────┘


Firstmate + Captain's Deck

Task → Agent → Worktree → PR → Validate → Land
           │                         │
           └────── Firstmate ────────┘
                         │
                  Human needed?
                         │
                  Captain's Call
                         │
                  Captain's Deck
```

Firstmate's project modes (`no-mistakes`, `direct-PR`, `local-only`, and optional **`+yolo`** merge autonomy) prepare the PR, keep ship branches aligned with `main`, work through conflicts, and run the configured validation pipeline while the task closes.
When policy allows `+yolo`, landing can happen without you clicking merge.

### What could that save?

The estimates below are **preliminary directional estimates, not benchmark results**.
They estimate **human orchestration overhead only**: checking agents, opening PRs, monitoring CI, updating branches, handling merge-flow interruptions, rerunning validation, and merging.
They do not include the actual coding time performed by the agent.

| Task effort | Traditional human touchpoints | With Firstmate | Potential actions removed | Estimated orchestration time saved |
| --- | ---: | ---: | ---: | ---: |
| **Small** — simple fix / small change | ~6–8 | ~1–3 | **4–6** | **~10–20 min** |
| **Medium** — normal feature / multi-file change | ~10–14 | ~2–4 | **7–10** | **~30–60 min** |
| **Large** — complex feature / migration / significant refactor | ~15–25 | ~3–6 | **10–20+** | **~1–2+ hours** |

The advantage compounds when several agents work at the same time.
For example, **five medium tasks** can create roughly **50–70 human coordination touchpoints** in a conventional workflow.
With Firstmate handling the routine delivery loop, that could fall to roughly **10–20 meaningful human interactions**.

> **Not necessarily less engineering work — dramatically less orchestration work.**

### Where Captain's Deck fits

Automation still needs a captain for real decisions.
When work requires human judgment, Firstmate exposes it as a **Captain's Call**.
Technical blockers and dependencies remain with their task owner and Firstmate; they do not create an approval question by themselves.
Everything else should keep moving without interrupting your flow.

Captain's Deck is that bridge: a kanban board where you can:

- **see the whole crew at a glance**,
- spot blocking work and Captain's Calls immediately,
- answer a Captain's Call in place — choose an option, add a note, queue the answer, and let Firstmate act on it,
- jump to the live Herdr pane when you actually need eyes on an agent,
- and leave everything else running without babysitting terminals.

The board stays read-only except for those guarded keyed answers, the steering they trigger, and a message the captain sends to Firstmate with `m`, so orchestration keeps running and you only touch what actually requires the captain.
Submitting an answer records the durable decision; a current keyed-answer intake wakes the owning home itself, while the Deck can supply a fallback steer for a secondmate-owned call's lane. On a first Reconcile, the Deck also binds itself as the captured source. The dialog spells those effects out before you queue, and `FM_FLOW_WAKE=0` skips the Deck's own lane steer.

**You steer the ship. Firstmate runs the crew. Captain's Deck shows you where your attention is actually needed.**

## The board

**Underway means unfinished work that has started, including blocked or paused work.**
Its total is not a count of agents actively working.
The `⛔` count beside a column total names the cards currently displaying a task or native-pane blocker.
Dependency gates use `⏳ dependency`; Captain's Call contains the authoritative human questions.
Press `d` on a selected card for a scrollable, read-only view of the reason, owning Firstmate home, worker, recorded dependency and next step.
Arrow keys, Page Up/Page Down or the wheel scroll; `Esc` closes it.
Blocker reasons preserve the full declaration available in the bounded task log, including coordination context that is unsuitable as a progress report.
An absent dependency owner is shown as unrecorded rather than guessed; Firstmate must supply that routing.
Keyed blocked/decision declarations remain visible until their exact key is resolved in the log; a later working declaration alone does not close them.

One board, every crew: an **All** tab merges the discovered homes, retaining each delivery's owning identity.
Each crew tab shows Charted Next, Underway, Captain's Call and Recent Landed.
All also includes Other Agents.
Completed workers stay Underway with a distinct integration phase until delivery, so no task disappears when Awaiting Merge is removed.
Most actions read state; Captain's Call answers, explicit messages and explicit explanation requests use their respective Firstmate intakes.
Opening a delivery also records a local read mark, without accepting or closing the task.

The fleet board folds a task that more than one home lists - a captain home
mirrors delegated work as `<mate>/<task>` while the mate's own home keeps
`<task>` - into the owning home's row, in the column that row reports, so one
task is one card. Captain's Call rows already fold that way for answer routing;
two same-named tasks in different repos (no matching namespace, run, or
worktree) stay separate.

| Column | Source |
| --- | --- |
| **Charted Next** | `gates` |
| **Underway** | all `in_flight` rows, including pending integration; phase, reason, dependency and next action distinguish the wait |
| **Captain's Call** | `decisions_open` - click a ticket to decide it in place |
| **Recent Landed** | `landed` — Firstmate's "Recently Landed": merged PRs, completed scouts, local-only merges (toggle visibility with `L`) |
| **Other Agents** | All tab only: every Herdr agent that is not a Firstmate pane, from `herdr api snapshot` |

Firstmate's own bearings has four sections (Underway, Charted Next, Captain's Call,
Recently Landed) and deliberately keeps run status out of the section split.
The optional `flow` projection supplies phase, full reason, dependency names/owners, waiting condition and next action.
Without it, Deck quotes retained declarations and labels missing ownership or confirmation explicitly.
A supervisor declaration is shown with its original age and TTL; it is never proof that a model is currently executing.

The board uses **Firstmate's own bounds** by default (`FM_BEARINGS_LANDED` = 6
newest per home, gates/in-flight = 20). `FM_FLOW_ALL=1` requests every row.
Completion dates can have only day precision: equal dates are ordered by task ID, not by inferred time or file modification date.
Use **H history** to find retained deliveries outside that recent cap, including from All.

## Reading deliveries

On All or a crew tab, Enter or a click on **Recent Landed** opens a scrollable delivery sheet.
`d` opens the same read-only sheet for a landed task; `o` remains the explicit action for an available live pane.
Cards quote retained outcome prose; the initial sheet contains only the readable home-authored summary.
Markdown presentation is removed, long recaps are explicitly abbreviated, and full retained content stays accessible.
Press **r / Informe** for the full report or **d / Detalles** for original context, identity, canonical fields, receipts and provenance.
Tab/Shift+Tab and Enter activate the same visible controls as a click; arrows, paging, Home/End and wheel scroll without skipping short viewports.
The date and relative age refresh independently of backend polling; exact source precision is preserved.
A closed study or subtask describes only its own delivery; Deck does not infer that the broader intent is finished.
Missing dates, reports, recaps and unavailable sources are labelled explicitly.

A home may optionally author `data/<task-id>/delivery.md` as UTF-8 Markdown.
Begin with a short result and its scope, then `Scope`, `Why`, `Evidence` (or `Checks`) and `Remaining` (or `Pending`) sections; Spanish headings are also accepted.
Include only established outcomes and link reports/receipts relative to that task directory.
This is human-curated prose, with no required metadata or generated conclusions.
Deck prefers a nonempty `delivery.md`, then `recap.md`, then existing `report.md` prose.
The card quotes an explicit `Result` / `Resultado` section when present, otherwise the first substantive paragraph; it never derives completion from the request title.
The reader retains the other summary, known application/package receipts, linked local text artifacts and canonical task fields.
Markdown, HTML and URLs are displayed as plain text; URLs never open automatically.
Artifact reads stay within that home's exact `data/<task-id>/` directory, reject traversal and symlinks, and accept only `.md`, `.txt` and `.json` files.
Limits are explicit: 512 KiB per artifact, 2 MiB total and 32 artifact paths; a refused/oversize artifact is labelled rather than silently truncated.

Press **H** or click **H history** for searchable **retained delivery history**, separate from the bounded recent column.
Type search terms (case and accents ignored) to filter task ids, full titles, canonical notes, dates, artifacts and source; arrows select, Page Up/Down page, Enter or click opens, Ctrl+U or Clear resets the search, Tab/Info shows full source details, and Esc returns.
The sheet supports arrows, Page Up/Down, Home/End, wheel and visible action controls, including small terminals such as 60×12.
History reads both the current backlog and the configured Markdown archive in the local home's `.tasks.toml` (for example `data/backlog.md` and `data/done-archive.md`, with `done_keep = 10`).
When available, `bin/fm-delivery-history.sh --json` supplies the public read-only history contract.
Older homes use the installed **tasks-axi / Node** canonical `parseConfigToml` and `parseBacklog` readers; dated archive headings are adapted in memory to Done sections.
No pruning, retention changes, new recent limits or backlog writes occur.
Current rows take precedence over archived copies, including reopened work; answered captain questions and reportless scouts stay out of deliveries.
This is the history still retained by the backend, not a promise of permanent storage.
Each index file is bounded to 16 MiB; a missing/oversize/unreadable source is labelled incomplete.
If the installed tasks-axi does not expose those readers, the history reports that dependency while recent sheets still show retained local reports.
Remote homes and other backends need a read-only completed-record/archive export; their recent snapshot text remains consultable with an explicit artifact/history limitation.
All combines retained history from discovered local homes and keeps each row's owning home through report reading and read marks.
Equal task IDs in different homes stay separate.
Unknown, remote or ambiguous ownership is explained without guessing a filesystem root.

A small **new/read** marker records whether this home/task delivery has been explicitly opened.
Only opening a sheet writes `deliveries-read.json` in the plugin's local state directory; painting, refreshing, hiding recent rows and searching do not mark deliveries read.
The marker persists across Deck sessions and is independent for each canonical home path/task id.
It is reading history, with no acceptance, approval or task-state effect.

The visible **e Pedir explicación a Firstmate** action sends only the resolved home/task identity and a stable request ID to `bin/fm-delivery-explain.sh`.
Opening the sheet checks status with `--status`, which never queues a request.
Repeated clicks and reopened sheets reuse that identity; queued, acknowledged, answered and saved-without-confirmed-notice remain distinct.
**s Estado** checks the receipt again, and Detalles exposes its identity and response.
An enqueue receipt does not prove Firstmate is running, and the action never approves, closes or merges the task or executes report content.
Older or remote homes show a clear unsupported-backend explanation.

## Other agents

The **All** tab also lists every Herdr agent that is not a Firstmate pane in an **Other Agents** column, alongside deliveries from discovered homes.
Blocked agents sort first, then done, working, and idle.
Each card shows the workspace, the agent, its terminal title, the time in its current state, and its tab and pane; click it to open that tab.
A pane counts as Firstmate's when a task's `state/<task>.meta` names it, when its working directory is a Firstmate home (the supervisor), or when it sits in a Firstmate task workspace (`└ <task> · p:<token>`).
The header counts the other agents on every tab and shows how many are blocked, so a waiting agent is visible from any crew.
Herdr does not timestamp state changes, so the deck times them itself: a state that was already there when the board opened shows as `for ≥<time>`.

## Remote second mates

A Firstmate home can live on another host ([remote second mates](https://github.com/kunchenguid/firstmate/blob/main/docs/remote-secondmates.md)), and the deck shows it as its own crew tab.
Remote routes come from the captain home's `data/secondmates.md` — the records with `host:`, `root:` and `home:` fields.
The snapshot goes through Firstmate's own transport, `bin/fm-on.sh <id> fm-bearings-snapshot.sh`, never raw SSH.
The result is cached like a local home (`bearings_secs`), so a host that is offline for a long time never blocks the board: the tab keeps the last snapshot and shows `· offline`, and the header names when it was last seen.
Remote tabs stay read-only: a Captain's Call on a remote home opens nothing, because the answer intake and the steer live in the local home.

Crew tabs show a live activity dot (`●` working/blocked, `○` agent present) and
the number of tickets on that board once it has been visited; a visited board
with no tickets shows `(0)`. **All** shows the visible fleet-wide
planned/running count after mirrored rows are folded; mate tabs count every
column including Landed when that column is visible.

## What each ticket shows

```text
╭─ demo-issue-197 ─────────────────────╮
│ ◐ validating                         │   live badge
│ claude-a·opus·xhigh                  │   account (or harness) · model · thinking effort
│ Add retry to the…                    │   title / summary / landed what
│ 9m 59s · ↓ 55.5k tokens · xhigh      │   total run time · tokens · thinking
│ ⌸ 4/my-app-feat…                     │   worktree (or ↗ PR artifact for landed)
╰──────────────────────────────────────╯
```

`claude-a` is the subscription account that runs the ticket.
For a local home, the deck reads it from the task meta and resolves store paths through that home's `config/crew-accounts.json`.
A ticket with no account record keeps its harness label, and a pi worker shows its provider.
Remote-home cards keep the harness label because their account metadata is not available locally.

Badges: `● shipping`, `◐ validating`, `⛔ blocked`, `⚑ decision` /
`⚑ pending`, `◍ awaits merge`, `⏸ parked` / `⏸ paused`, `⛔ failed`,
`✓ done` / `✓ landed`, `· queued`.
Live state comes from `herdr api snapshot`; activity and review state come from
Firstmate's bearings snapshot and the home's `state/<task>.status` tail.
When Firstmate cannot confirm runtime activity, the badge names available native evidence, such as `Herdr working`, or says `unknown`.
This does not change Firstmate's unknown semantic verdict.
Cards quote the complete available wait reason with an explicit full-context action when space requires an excerpt.
Full context keeps Firstmate's semantic state, the native Herdr observation, account/model/thinking, run totals and event provenance separate.
Unknown confirmation remains visible even if a native pane looks busy.
Captain's Call retains its authored question and answer intake; technical blocks do not become human approval requests.
A reported completion alone does not establish integration readiness; it remains Underway with its uncertainty visible.
Use `--once` to inspect the original Firstmate state and diagnostic, Herdr observation and reported status separately.
While an agent is **shipping**, **validating**, or **blocked**, the
**full task context** carries total run time and total token use in the same
style as Herdr's agent sidebar (wall time since task spawn when available;
tokens from detection or cumulative Pi session usage). A `doing` value that
only repeats the badge (`validating`, `validating: …`) or the generic
`harness busy …` line is dropped, so the row shows the totals alone; a `doing`
that adds real detail keeps it before the totals. Validating and blocked rows
also carry the thinking effort when the row has room
(`9m 59s · ↓ 55.5k tokens · xhigh`).

## Controls

The footer shows the frequent actions - `? help`, `L - Show/Hide Landed`, and
`r - Refresh board` - and each one is clickable. The help modal (click
`? help` or press `?`) lists every binding: `←→` / `h l` move between columns,
`↑↓` / `j k` move between cards, `pgup`/`pgdn` or the wheel scroll a column,
clicking a crew tab switches mate, `enter` or a click opens a Captain's Call
ticket or a Landed delivery, `H` searches retained deliveries, `o` opens the selected agent pane, `m` opens the Message Firstmate box, `1-9` / `tab` switch crew
(All = fleet), `L` shows or hides the Landed column, `r` refreshes the board,
and `q` quits. `esc` or `?` closes the modal, and a click outside it closes it
too.

## Keeping up with Firstmate updates

Firstmate updates itself (`updatefirstmate`, fleet sync) and this deck reads
its live surfaces from the home, so drift usually shows up as empty or quiet
cards rather than an error. Three guards keep that visible:

- **Contract check** - `scripts/fm-contract-check.sh --home ~/firstmate` (or
  `python3 scripts/fm_contract_check.py --home ~/firstmate --json`) runs the
  home's `bin/fm-bearings-snapshot.sh --json --fields paths` and checks its
  sections and selected item and task-meta fields. It also checks status lines,
  decision cards, and the `fm-captain-hold.sh` / `fm-send.sh` intakes that the
  deck reads. Exit code 1 lists the missing surface; run it after an
  `updatefirstmate`.
- **Recorded fixtures** - `tests/fixtures/` keeps a sanitized snapshot and a
  minimal home. `tests/test_fixture_board.py` proves the recorded shape still
  projects into the same columns, and `tests/test_fm_contract_check.py` proves
  the checker itself catches a dropped field.
- **Manual fixture workflow** - `.github/workflows/firstmate-contract.yml` runs
  the recorded producer contract check when manually dispatched.
  It has read-only repository permissions.
  Run the local contract check above to assess your installed Firstmate version.

For field reports, `FM_FLOW_DEBUG=/tmp/fm_flow_debug.log` stamps the deck
revision and each home's Firstmate revision on every collector pass, so a log
pins the exact pair.

## Answering a Captain's Call ticket

Clicking a Captain's Call ticket (or pressing `Enter` on it) opens its decision
card as a modal, composed from the same `fm-bearings-board.v1` card the Lavish
bearings board renders: the type badge and repo, the title, the `ABOUT` /
`DECIDE` context, every authored option with its hint, the `REC` mark on the
recommended one, a freeform note row, and **Queue answer**.

- `↑`/`↓` (or `j`/`k`) move, `space` picks or clears an option, typing edits
the note, `Tab` jumps between the options and the note, `Enter` queues, and
`esc` closes without answering.
- The answer is piped to Firstmate's one keyed-answer intake
(`bin/fm-captain-hold.sh answers`) in the home that owns the ticket, with this
Deck as its provenance. The key, answer and label are flattened first, so a
newline or tab in card text can never become a second answer row. A card that
declares `close: "release"` releases the gated work instead of completing it.
- The dialog shows the option's value whenever it differs from its label, names
the card-declared close on the submit button (`Queue answer · releases hold`),
and prints one line of what a submit does beyond the record: the exact key, the
close or release, and, for a secondmate-owned call, the lane that will be
steered.
- After the record lands, an intake that settles answers itself (it defines
`wake_answer_owner`) wakes the owning home, and the Deck adds no second steer.
For a reconcile request, or an answer recorded by an older intake, the Deck can
steer a secondmate-owned call through the parent home's lane inbox
(`fm-send.sh`). A main-home-owned `(main)` call has no lane, so the Deck sends
it no steer. A lane-steer problem is shown beside the queued state; the recorded
answer is never reversed. Set `FM_FLOW_WAKE=0` (or `wake_owner=0` in the config
dir) to skip the Deck's own lane steer.
- `Reconcile` is the reserved value: it files a durable reconcile request
through `reconcile-requests`, binding `herdr-firstmate-flow` as its captured
source on first use, and never closes anything by itself. The call leaves
Captain's Call immediately - the snapshot buckets it `reconciling` and shows it
under Charted Next as `reconcile requested <time>` - and returns only if the
owner finds it still active.
- Card content is read from the durable store written when the call is raised
  (`state/decision-cards/<task>.json`), then the live board, then the composed
  payload history. The board build also merges that store first, so the authored
  raise-time card remains authoritative across rebuilds.
- When no composed card exists for a ticket, the dialog still opens with its
durable title, its hold reason as the `ABOUT` line, a freeform answer, and
`Reconcile`.
- Nothing is resolved by the board itself: every guard, the durable decision,
and the close all live in Firstmate.

## Message Firstmate

Press `m` to open the Message Firstmate box, type a message, and press `Enter` to send it (`Esc` closes the box without sending).
The deck submits the text with `herdr agent prompt` to the Firstmate supervisor pane of the tab on screen, the same as typing it in that pane; the All tab sends to the captain home.
The supervisor is the Herdr agent whose working directory is the home path, and the box shows that pane before you send.
If Firstmate is waiting at an approval or question, Herdr refuses the message (`agent_blocked`), and the box keeps the text so you can answer that prompt first.
If Firstmate is working, Claude Code holds the message until the current turn ends.
An empty message and a message over 2000 characters are refused, and line breaks become spaces.

## Freshness: only what changed is refreshed

The board never redraws itself wholesale.

- **Live tick (2s):** the active board rebuilds its badges from cheap sources —
  one `herdr api snapshot` (agents, panes, workspace and tab labels), each task's `state/<id>.meta` and the tail of
  `state/<id>.status` (both cached by mtime).
- **Bearings (20s):** the expensive `fm-bearings-snapshot.sh` run only happens
  when the cached snapshot is older than `FM_FLOW_BEARINGS_SECS`, when you switch
  to a crew that has no cached data, or when you press `r`.
- **Diff repaint:** only the screen lines that actually changed are written, so a
  badge updating does not disturb your scroll position, selection, or the rest of
  the board.
- **Only the active crew** is refreshed; other crews cost nothing while you look
  at one board.

## Interaction

| Action | What happens |
| --- | --- |
| Click a **crew name** (top row) | Switch instantly; **All** loads every mate's bearings; other tabs use cached cards first, then refresh |
| Click a **ticket** | Focus the ticket's Herdr tab/pane, which selects that agent in the Herdr agents sidebar |
| Click a Captain's Call **ticket** | Open its decision card modal and queue the captain's answer |
| Click an **Other Agents** card | Focus that agent's Herdr tab |
| Click a ticket with no live pane | Footer explains it, e.g. `demo-issue-198: no live pane · (no worktree yet)` |
| `1`…`9`, `Tab` / `Shift+Tab`, `[` / `]` | Switch crew |
| `←`/`→` or `h`/`l` | Move between columns |
| `↑`/`↓` or `j`/`k` | Move between tickets (the view follows the selection) |
| Mouse **wheel over a column** | Scroll that column smoothly (3 rows per notch) |
| `Shift`+wheel | Scroll every column together |
| `PgUp`/`PgDn`, `g`/`G` | Page / jump within the selected column |
| `Enter` | Open a call, delivery or full task context; Other Agents opens its pane |
| `o` | Open the selected ticket's agent pane, including a Captain's Call one |
| `d` | Read the selected ticket's full available reason, owner, dependency and next step |
| `m` | Open the Message Firstmate box; `Enter` sends, `Esc` closes |
| `r` / `q` | Force a bearings refresh / quit |
| `L` | Show/hide the Landed column |

Input repaints immediately (no waiting for the next data tick), and a burst of
wheel events is coalesced into a single repaint. Scrolling moves by terminal
rows, so cards clip at the edges instead of jumping a whole 12-row card.

Ticket → pane mapping comes from the task's `state/<task>.meta`
(`herdr_pane_id`, `herdr_tab_id`, `herdr_workspace_id`), with a fallback to
matching the worktree path against live pane working directories. The `herdr`
binary is resolved from `HERDR_BIN_PATH`, then `PATH`, then `~/.local/bin/herdr`
(plugin panes inherit a minimal `PATH`).

## Theme

Ticket borders, badges, and column titles use the terminal's **ANSI palette**
(basic 16 colours, no hardcoded RGB or 256-colour indexes), so they follow
whatever palette the Herdr `[theme]` setting gives the panes.

## Requirements

- [Herdr](https://herdr.dev/) >= 0.9.0
- [Firstmate](https://github.com/kunchenguid/firstmate) homes with `bin/fm-bearings-snapshot.sh`
- `python3` (standard library only), no `jq` required

## Home discovery

Homes are discovered in this order (first match wins per path):

1. `FM_FLOW_HOMES_JSON` JSON label/path object (supports spaces) and `FM_FLOW_HOMES` — `label=path label=path`
2. `homes.conf` in the plugin config directory — one `label=path` per line
3. `FM_HOME` or the legacy `fm_home` config file
4. `~/firstmate` plus `~/.treehouse/*/*/firstmate` worktree homes
5. Remote routes in the captain home's `data/secondmates.md` (records with `host:`, `root:` and `home:`), shown through `fm-on.sh`

The explicit list is additive by default: setting `FM_FLOW_HOMES` or
`homes.conf` labels the homes it names, while the scan still runs. Set
`FM_FLOW_HOMES_ONLY=1` (or `homes_only=1` in the config dir) to make the
explicit list the only source; with nothing configured it falls back to the
scan rather than showing an empty board.

On a large fleet the bearings walk is the slow part: it has been measured
at ~112s for a captain home with 58 in-flight tasks, so `bearings_timeout`
may need raising above its 180s default and `bearings_secs` set so the walk
is not repeated on every refresh.

A home is shown when it has task directories **or** a live Herdr agent running
in it. An unleased spare treehouse worktree - no lease holder, no presentation
label, no live agent - is hidden, because it is a slot rather than a crew. Labels come from the treehouse lease holder
(`~/.treehouse/*/treehouse-state.json`) or a task's
`state/*.herdr-presentation` `parent_label`, so secondmates appear as e.g.
`2ndmate-demo`.

Plugin config lives in:

```text
~/.config/herdr/plugins/config/herdr-firstmate-flow/
  fm_home        # legacy single-home config (still honored)
  homes.conf     # optional label=path list
  show_landed    # optional: 0 hides the Landed column
  wake_owner     # optional: 0 skips the Deck's own secondmate lane steer
  homes_only     # optional: 1 limits discovery to FM_FLOW_HOMES/homes.conf
  bearings_secs  # optional: seconds a bearings snapshot stays cached
  bearings_timeout # optional: seconds allowed for one snapshot walk
  debug_log      # optional: a path, or an empty file for <config>/debug_log.log
  flow-overlay.panes  # internal: recorded overlay pane ids
  deck-flow.panes     # internal: recorded captain's deck pane ids
```

Environment knobs:

| Variable | Default | Meaning |
| --- | --- | --- |
| `FM_FLOW_TICK_SECS` | `2` | Live badge/agent refresh |
| `FM_FLOW_BEARINGS_SECS` | `20` | Bearings snapshot TTL |
| `FM_FLOW_WHEEL_ROWS` | `3` | Rows moved per wheel notch |
| `FM_FLOW_LANDED_LIMIT` | `10` | Safety cap on landed cards (Firstmate's own bound is 6/home) |
| `FM_FLOW_SHOW_LANDED` | `1` | Show the Landed column (`0` = hide it, also toggleable with `L`) |
| `FM_FLOW_ALL` | `0` | `1` = request every row from bearings instead of Firstmate's bounds |
| `FM_FLOW_HOMES` | — | Explicit `label=path` crew list |
| `FM_FLOW_HOMES_ONLY` | `0` | `1` = discover only `FM_FLOW_HOMES`/`homes.conf` homes |
| `FM_FLOW_WAKE` | `1` | `0` = skip the Deck's own secondmate lane steer (an intake that wakes the owner still does) |
| `FM_FLOW_DEBUG` | — | Append click/collector debug lines to this file |

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow, conventions, and how to run the checks.

Report bugs, ask questions, or suggest features with a [new GitHub issue](https://github.com/cbriseno/captains-deck/issues/new) (templates optional; blank issues are allowed).

## Related projects

- [Herdr](https://herdr.dev/) — terminal workspaces, panes, and the agent host
  this plugin extends.
- [Herdr GPUI](https://github.com/penso/herdr-gpui) — native Rust/GPUI Herdr
  client for Herdr.
- [Firstmate](https://github.com/kunchenguid/firstmate) — agent distro that runs
  the crew, closes worktrees into PRs (and optional yolo release), and owns the
  bearings snapshot and Captain's Call intake this board projects.
- [Pi](https://pi.dev/) — one of the supported primary harnesses; see
  [What each ticket shows](#what-each-ticket-shows) for its provider and model
  display.
