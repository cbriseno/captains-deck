#!/usr/bin/env python3
"""Firstmate Flow - multi-home kanban TUI for Herdr.

Shows every Firstmate home (captain + secondmates) as a crew tab, projected
into four visible columns: Charted Next, Underway, Captain's Call and Landed.
Pending integration remains Underway with an explicit phase.

Cards prioritize outcomes, wait reasons, dependency owners and next actions.
Account, model and thinking effort remain available in full task context. Click / Enter on a
Landed ticket opens its read-only delivery sheet; H searches retained delivery
history. Other worker tickets focus their Herdr pane, while o explicitly
opens an available live pane.

Captain's Call is the one column that acts: clicking (or pressing Enter on) a
ticket opens its composed board card as a decision dialog, and the captain's
choice is piped to firstmate's one keyed-answer intake
(``bin/fm-captain-hold.sh answers``) - or to its reconcile-request intake for
the reserved ``reconcile`` value. An intake that settles answers wakes the
owning home itself, and the Deck adds no second steer. For an intake without
its own wake for the selected action, the Deck can steer a secondmate-owned
call through the parent home's lane inbox (``bin/fm-send.sh``). A main-home-owned
call has no lane, so the Deck sends it no steer. Set ``FM_FLOW_WAKE=0`` to skip
the Deck's own lane steer.
Reconcile binds this Deck as its captured source on first use, and keyed lines
flatten CR/LF/TAB so card text can never split into a second answer row.
Delivery explanation requests use an identity-only, deduplicated intake; all
other artifact and history operations remain read-only apart from local read marks.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import select
import stat
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from datetime import date, datetime, timezone
from typing import Any

# The shared model is also loaded by filename by plugins and fixture tools.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deck_platform import endpoint_path, file_lock, safe_file_reads, shell_command

# ----------------------------------------------------------------------------
# constants
# ----------------------------------------------------------------------------

# Firstmate's own four bearings sections: gates (Charted Next), in_flight
# (Underway), decisions_open (Captain's Call), landed (Recently Landed).
# The legacy awaiting_merge key stays empty for inspection compatibility.
# Integration is a phase of Underway, never an extra visible column.
COLUMNS = [
    ("charted", "Charted Next"),
    ("underway", "Underway"),
    ("captains_call", "Captain's Call"),
    ("awaiting_merge", "Awaiting Merge"),
    ("landed", "Landed"),
]
# Keep the legacy empty bucket for consumers of --once, but give its space to
# the four visible columns. Finished workers remain Underway until landed.
CARD_ROWS = 12

# The Lavish bearings board composes every open call into a card with ABOUT /
# DECIDE context and authored options. The captain's deck mirrors that card in a
# dialog and turns the captain's choice into the keyed line firstmate's one
# answer intake owns (bin/fm-captain-hold.sh answers). This is a channel, not a
# decision-maker: it never resolves a task itself, and the reserved `reconcile`
# value goes to the separate reconcile-request intake, never to `answers`.
# Footer hints: clickable quick actions on the bottom line (label, action).
_FOOTER_HINTS = (
    ("? help", "help"),
    ("d details", "details"),
    ("H history", "history"),
    ("L - Show/Hide Landed", "landed"),
    ("A - Show/Hide answered", "answered"),
    ("r - Refresh board", "refresh"),
)

DECISION_SOURCE_ID = "herdr-firstmate-flow"
DECISION_SOURCE = "herdr-firstmate-flow captain's deck"
ANSWER_LIMIT = 512  # bytes, the board's own bound
RECONCILE_OPTION = {
    "value": "reconcile",
    "label": "Reconcile",
    "hint": (
        "Re-check the latest state, then close this with evidence "
        "or keep it open with a note"
    ),
}

# The intake's own key bound. A key outside it is skipped there without word,
# so the dialog refuses it visibly instead.
KEY_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")
# CR/LF/TAB would split one keyed line into extra answer rows at the intake,
# which sanitizes each field only after it has split stdin into rows.
_FIELD_BREAK_RE = re.compile(r"[\r\n\t]+")

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
REV = "\x1b[7m"
CLEAR = "\x1b[2J\x1b[H"


def fg(n: int) -> str:
    """ANSI-16 SGR colour. Herdr drives the pane palette from its [theme]
    setting, so basic colours follow the user's theme instead of hardcoding
    256-colour indexes."""
    return f"\x1b[{n}m"


C_BORDER = 90   # bright black
C_ACCENT = 36   # cyan
C_TITLE = 36    # cyan
C_DIM = 90      # bright black
C_OK = 32       # green
C_WARN = 33     # yellow
C_BAD = 31      # red
C_DECIDE = 35   # magenta
C_REVIEW = 36   # cyan
C_QUEUE = 90    # bright black

HOMES_REDISCOVER_SECS = 60.0

# data/ entries that are not task directories
NON_TASK = re.compile(
    r"^(backlog|charter|done-archive|learnings|projects|captain|crew|README|"
    r"stow-receipt|bearings-board-payload).*",
    re.I,
)


def _debug(msg: str) -> None:
    path = os.environ.get("FM_FLOW_DEBUG")
    if not path:
        return
    try:
        with open(path, "a") as fh:
            fh.write(f"{time.time():.3f} {msg}\n")
    except OSError:
        pass


_REV_CACHE: dict[str, str] = {}


def _git_revision(path: str) -> str:
    """Short commit for a checkout; "" when it is not a git work tree."""
    if not path:
        return ""
    if path in _REV_CACHE:
        return _REV_CACHE[path]
    rev = ""
    try:
        proc = subprocess.run(
            ["git", "-C", path, "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"),
            stdin=subprocess.DEVNULL,
        )
        if proc.returncode == 0:
            rev = (proc.stdout or "").strip()
    except Exception:
        rev = ""
    _REV_CACHE[path] = rev
    return rev


def _deck_revision() -> str:
    return _git_revision(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value, default: float = 5.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _flatten_field(value) -> str:
    """Collapse CR/LF/TAB so a title, answer or note can never become a second
    keyed-answer row at the intake."""
    return _FIELD_BREAK_RE.sub(" ", str(value or "")).strip()


# Live badge/agent refresh. FM_FLOW_REFRESH_SECS is honored as a legacy alias.
TICK_SECS = max(
    0.25,
    min(
        60.0,
        _float(
            os.environ.get("FM_FLOW_TICK_SECS") or os.environ.get("FM_FLOW_REFRESH_SECS"),
            2.0,
        ),
    ),
)
# How often the expensive bearings snapshot (columns/paths) is rebuilt.
def _cfg_value(name: str) -> str:
    """Read a plain scalar from the plugin config dir (same convention as show_landed)."""
    try:
        with open(os.path.join(_plugin_config_dir(), name)) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _cfg_or_env(file_name: str, env_name: str) -> str:
    return os.environ.get(env_name) or _cfg_value(file_name)


def _bearings_ttl() -> float:
    return max(2.0, min(3600.0, _float(_cfg_or_env("bearings_secs", "FM_FLOW_BEARINGS_SECS"), 20.0)))
# Snapshot walk budget: a large fleet can take minutes (measured 112s on a
# 58-task captain home), so this must be configurable, not a bare 25s.
def _bearings_timeout() -> float:
    return max(5.0, _float(_cfg_or_env("bearings_timeout", "FM_FLOW_BEARINGS_TIMEOUT"), 180.0))
# Rows scrolled per wheel notch (cards are 12 rows tall, so 3 feels smooth).
WHEEL_ROWS = max(1, min(8, _int(os.environ.get("FM_FLOW_WHEEL_ROWS"), 3)))
# Landed grows forever in Firstmate's archive; show only the newest few.
LANDED_LIMIT = max(0, _int(os.environ.get("FM_FLOW_LANDED_LIMIT"), 10))


def _on(name: str, default: bool = False) -> bool:
    val = os.environ.get(name, "")
    if not val:
        val = str(default)
    return val.strip().lower() in ("1", "true", "yes", "on")


def show_landed_default() -> bool:
    """Landed is a Firstmate section, but it is a "what shipped" record rather
    than work in flight, so it can be hidden (env or plugin config file)."""
    val = os.environ.get("FM_FLOW_SHOW_LANDED")
    if val is None:
        try:
            with open(os.path.join(_plugin_config_dir(), "show_landed")) as fh:
                val = fh.read().strip()
        except OSError:
            val = ""
    return (val or "1").strip().lower() not in ("0", "false", "no", "off")


def set_show_landed_pref(show: bool) -> None:
    """Persist landed-column visibility for this and later board sessions."""
    os.environ["FM_FLOW_SHOW_LANDED"] = "1" if show else "0"
    path = os.path.join(_plugin_config_dir(), "show_landed")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("1\n" if show else "0\n")
    except OSError:
        pass


def _config_truthy(name: str, filename: str, default: bool) -> bool:
    """Env-first boolean, then the plugin config file, then the default."""
    val = os.environ.get(name)
    if val is None:
        try:
            with open(os.path.join(_plugin_config_dir(), filename)) as fh:
                val = fh.read().strip()
        except OSError:
            val = ""
    fallback = "1" if default else "0"
    return (val or fallback).strip().lower() in ("1", "true", "yes", "on")


def hide_answered_default() -> bool:
    """Whether an answered Captain's Call hides until its owner takes the work.

    The deck answers a call and Firstmate keeps a live captain hold until the
    owning agent starts, so the same row returns with no new question.
    FM_FLOW_HIDE_ANSWERED=0, or hide_answered_calls=0 in the plugin config dir,
    keeps every answered row on the board."""
    return _config_truthy("FM_FLOW_HIDE_ANSWERED", "hide_answered_calls", True)


# ----------------------------------------------------------------------------
# answered calls: the ledger that keeps an answered row off the board
# ----------------------------------------------------------------------------

ANSWERED_LEDGER = "answered-calls.json"
# A weekend must not expire a record: a Friday answer stays hidden on Monday.
ANSWERED_TTL_SECS = 7 * 24 * 3600


def _plugin_state_dir() -> str:
    """The plugin's own state directory. Herdr hands the path to plugin
    processes; the fallback matches the directory Herdr uses for this plugin."""
    return os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state"),
        "herdr", "plugins", DECISION_SOURCE_ID,
    )


def answered_ledger_path() -> str:
    return os.path.join(_plugin_state_dir(), ANSWERED_LEDGER)


def answered_call_key(home_path: str, task: str) -> str:
    return json.dumps([os.path.realpath(home_path), task], separators=(",", ":"))


def _answered_call_task(key: str) -> str:
    if KEY_RE.fullmatch(key):
        return key
    try:
        parts = json.loads(key)
    except ValueError:
        return ""
    if (isinstance(parts, list) and len(parts) == 2
            and all(isinstance(part, str) for part in parts)
            and os.path.isabs(parts[0]) and KEY_RE.fullmatch(parts[1])
            and key == answered_call_key(*parts)):
        return parts[1]
    return ""


def load_answered_calls() -> dict[str, dict]:
    """Read home/task records and legacy task-only records."""
    try:
        with open(answered_ledger_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key): value
        for key, value in data.items()
        if isinstance(key, str) and _answered_call_task(key) and isinstance(value, dict)
    }


def save_answered_calls(records: dict[str, dict]) -> None:
    path = answered_ledger_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError as exc:
        _debug(f"answered-calls ledger write failed: {exc!r}")


def call_digest(key: str, title: str, options, close: str) -> str:
    """A stable identity for the question the captain answered.

    The record's generated stamp is rewritten every time the board re-composes
    an open call (measured 2026-10-16: the same three cards were rewritten at
    12:09 and again at 12:27 with unchanged content), so the stamp alone cannot
    tell the answered question from a new one. The content can."""
    values = sorted(
        _flatten_field(opt.get("value")) for opt in (options or []) if isinstance(opt, dict)
    )
    payload = "\x1f".join(
        [_flatten_field(key), _flatten_field(title), ",".join(values), close or ""]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def answer_match_field() -> str:
    """Which field decides that a row is the one the captain answered.

    The captain chose the generated stamp. FM_FLOW_ANSWER_MATCH=content compares
    the question's own content instead, which survives a re-compose."""
    value = (os.environ.get("FM_FLOW_ANSWER_MATCH") or "").strip().lower()
    return "content" if value == "content" else "generated"


def answered_row_hidden(record, stamp: str, digest: str, now: float) -> bool:
    """Whether one Captain's Call row hides as already answered."""
    if not isinstance(record, dict):
        return False
    answered_at = _float(record.get("answered_at"), 0.0)
    if answered_at <= 0 or now - answered_at > ANSWERED_TTL_SECS:
        return False
    if answer_match_field() == "content":
        return bool(digest) and record.get("digest") == digest
    return bool(stamp) and record.get("generated") == stamp


def record_answered_call(dialog: "DecisionDialog") -> None:
    """Remember an answered call, so the board hides it until its owner takes
    the work. A reconcile request is not an answer and writes nothing."""
    if not dialog.home_path or not KEY_RE.fullmatch(dialog.key):
        return
    records = load_answered_calls()
    records.pop(dialog.key, None)
    records[answered_call_key(dialog.home_path, dialog.key)] = {
        "home": dialog.home_path,
        "title": _flatten_field(dialog.title),
        "answer": _flatten_field(dialog.display_answer()),
        "generated": dialog.stamp,
        "digest": dialog.digest,
        "answered_at": time.time(),
    }
    save_answered_calls(records)


def wake_owner_default() -> bool:
    """Whether the Deck can steer a secondmate-owned call's lane after a submit
    the intake does not wake it for (default on). FM_FLOW_WAKE=0, or
    wake_owner=0 in the plugin config dir, skips the Deck's own lane steer."""
    return _config_truthy("FM_FLOW_WAKE", "wake_owner", True)


def homes_only_default() -> bool:
    """Whether discovery is limited to FM_FLOW_HOMES / homes.conf (default
    off: the explicit list adds homes on top of the usual scan)."""
    return _config_truthy("FM_FLOW_HOMES_ONLY", "homes_only", False)


def board_ticket_count(cols: dict[str, list], totals: dict[str, int]) -> tuple[int, int]:
    """Return (all tickets, landed-only) using bearings totals when present."""
    landed = totals.get("landed", len(cols.get("landed") or []))
    total = sum(totals.get(key, len(cols.get(key) or [])) for key, _ in COLUMNS)
    return total, landed


# Crew tab that merges cards already bound to their real source homes.
ALL_CREW_LABEL = "All"
ACTIVE_BUCKETS = tuple(key for key, _ in COLUMNS if key != "landed")


def is_aggregate_home(home: Home | None) -> bool:
    return home is not None and home.kind == "all"


def is_remote_home(home: Home | None) -> bool:
    """True for a home reached through a remote route (fm-on.sh), not a path."""
    return home is not None and bool(getattr(home, "route", ""))


def real_homes(homes: list[Home]) -> list[Home]:
    return [h for h in homes if not is_aggregate_home(h)]


def inject_all_crew(homes: list[Home]) -> list[Home]:
    if not homes or any(h.label == ALL_CREW_LABEL for h in homes):
        return homes
    return [Home(ALL_CREW_LABEL, "", "all")] + homes


def fleet_planned_running_count(
    homes: list[Home],
    counts: dict[str, int],
    landed_counts: dict[str, int],
    duplicates: int = 0,
) -> int | None:
    """Tickets in Charted/Underway/Captain's Call/Awaiting Merge across the fleet.

    ``duplicates`` is the number of mirrored rows the fleet merge folded away
    (a task listed by both its owning home and a parent home), so the All tab
    count matches the rows the board actually shows.
    """
    total = 0
    seen = False
    for home in real_homes(homes):
        count = counts.get(home.label)
        if count is None:
            continue
        seen = True
        total += max(0, count - landed_counts.get(home.label, 0))
    return max(0, total - duplicates) if seen else None


def _pick_captains_call_duplicate(cards: list[Card]) -> Card:
    """Keep the explicitly owned row when a parent mirrors a home's call."""
    explicit = [c for c in cards if c.owner and c.owner != "(main)"]
    return explicit[0] if explicit else cards[0]


def _captains_call_identity(card: Card) -> tuple:
    """One call is one task in one answering home.

    A parent's mirror of a delegated call already resolves to the owning
    home, so it shares that home's identity. Same-named tasks in independent
    homes, and rows whose answering home is unknown, never fold together.
    """
    identity = getattr(card, "delivery_identity", "")
    return (identity or id(card), card.task)


def _dedupe_captains_call(cards: list[Card]) -> list[Card]:
    groups: dict[tuple, list[Card]] = {}
    for card in cards:
        groups.setdefault(_captains_call_identity(card), []).append(card)
    return [
        group[0] if len(group) == 1 else _pick_captains_call_duplicate(group)
        for group in groups.values()
    ]


# Buckets the fleet merge folds duplicate rows in. Captain's Call is deduped
# separately (its answer must route to exactly one home) and Landed rows stay
# per-crew.
FLEET_FOLDED_BUCKETS = ("charted", "underway", "awaiting_merge")


def _owner_prefix(task_id: str) -> str:
    """The mate namespace in a delegated task id (``mate/task``)."""
    return task_id.split("/", 1)[0] if "/" in task_id else ""


def _run_ref(doing: str) -> str:
    """The Firstmate run id in a card's ``doing`` line, when it carries one."""
    match = re.search(r"run:\s*(\S+)", doing or "")
    return match.group(1) if match else ""


def _same_path(a: str, b: str) -> bool:
    return bool(a) and bool(b) and os.path.realpath(a) == os.path.realpath(b)


def _is_owner_row(card: Card, homes: list[Home]) -> bool:
    """Whether this row comes from the home its own task id points at."""
    prefix = _owner_prefix(card.id)
    if not prefix:
        return True
    owner = resolve_owner_home(homes, prefix, None)
    return owner is not None and _same_path(owner.path, card.home_path)


def _same_fleet_task(a: Card, b: Card, homes: list[Home]) -> bool:
    """Whether two rows from different homes describe one Firstmate task.

    A task can be listed twice in the fleet: the captain home mirrors work it
    delegated as ``<mate>/<task>`` while the mate's own home keeps ``<task>``.
    Beyond the shared task slug, only recognised evidence folds the rows: the
    namespace resolving to the other row's home, a matching Firstmate run id,
    or a matching worktree. Two same-named tasks in different repos stay apart.
    """
    if a.task != b.task:
        return False
    if _same_path(a.home_path, b.home_path):
        return True
    for card, other in ((a, b), (b, a)):
        prefix = _owner_prefix(card.id)
        if not prefix:
            continue
        owner = resolve_owner_home(homes, prefix, None)
        if owner is not None and _same_path(owner.path, other.home_path):
            return True
    run_a, run_b = _run_ref(a.doing), _run_ref(b.doing)
    if run_a and run_a == run_b:
        return True
    return bool(a.worktree) and bool(b.worktree) and _same_path(a.worktree, b.worktree)


def _prefer_active_row(cards: list[Card], homes: list[Home]) -> Card:
    """Pick the row that should represent a folded task on the fleet board.

    The owning home's own row wins over the parent home's mirror; then the
    further-along bucket; then the row with live agent evidence.
    """

    def depth_rank(card: Card) -> int:
        return {"awaiting_merge": 2, "underway": 1}.get(card.bucket, 0)

    def info_rank(card: Card) -> tuple:
        return (
            1 if card.live_status in ("working", "blocked") else 0,
            1 if card.pane_id else 0,
            1 if (card.run_elapsed or card.run_tokens) else 0,
        )

    return max(
        cards,
        key=lambda c: (
            1 if _is_owner_row(c, homes) else 0,
            depth_rank(c),
            info_rank(c),
        ),
    )


def _fold_duplicate_active_rows(
    cols: dict[str, list[Card]], totals: dict[str, int], homes: list[Home]
) -> None:
    """Fold rows that describe one task into the owning home's row.

    Firstmate's captain home lists delegated work as ``<mate>/<task>`` and the
    mate's own home lists ``<task>``, so the unfiltered fleet merge showed the
    same ticket twice. The kept row decides the column; ``totals`` drops the
    folded rows so the All tab badge matches the board.
    """
    indexed = [
        (bucket_key, card)
        for bucket_key in FLEET_FOLDED_BUCKETS
        for card in cols.get(bucket_key) or []
    ]
    if len(indexed) < 2:
        return
    by_task: dict[str, list[int]] = {}
    for i, (_bucket_key, card) in enumerate(indexed):
        by_task.setdefault(card.task, []).append(i)

    dropped: set[int] = set()
    for members in by_task.values():
        if len(members) < 2:
            continue
        clusters: list[list[int]] = []
        for i in members:
            hits = [
                cluster
                for cluster in clusters
                if any(
                    _same_fleet_task(indexed[i][1], indexed[j][1], homes)
                    for j in cluster
                )
            ]
            if not hits:
                clusters.append([i])
                continue
            merged = [i]
            for cluster in hits:
                merged.extend(cluster)
                clusters.remove(cluster)
            clusters.append(merged)
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            keep = _prefer_active_row([indexed[i][1] for i in cluster], homes)
            dropped.update(i for i in cluster if indexed[i][1] is not keep)

    if not dropped:
        return
    for bucket_key in FLEET_FOLDED_BUCKETS:
        kept = [
            card
            for i, (bucket, card) in enumerate(indexed)
            if bucket == bucket_key and i not in dropped
        ]
        gone = len(cols.get(bucket_key) or []) - len(kept)
        if not gone:
            continue
        cols[bucket_key] = kept
        totals[bucket_key] = max(0, totals.get(bucket_key, len(kept)) - gone)


def merge_fleet_columns(
    parts: list[tuple[Home, dict[str, list[Card]], dict[str, int]]],
) -> tuple[dict[str, list[Card]], dict[str, int]]:
    """Union active columns and retain recent deliveries with their provenance."""
    cols: dict[str, list[Card]] = {key: [] for key, _ in COLUMNS}
    totals: dict[str, int] = {key: 0 for key, _ in COLUMNS}
    fleet_homes = real_homes([h for h, _, _ in parts])
    order = {h.label: i for i, h in enumerate(fleet_homes)}

    def sort_key(card: Card) -> tuple:
        label = next((h.label for h, _, _ in parts if h.path == card.home_path), "")
        return (order.get(label, 99), card.task)

    for home, home_cols, home_totals in parts:
        for key in ACTIVE_BUCKETS:
            bucket = list(home_cols.get(key) or [])
            cols[key].extend(bucket)
            totals[key] += home_totals.get(key, len(bucket))
        totals["landed"] += home_totals.get("landed", len(home_cols.get("landed") or []))

    _fold_duplicate_active_rows(cols, totals, fleet_homes)

    for key in ACTIVE_BUCKETS:
        cols[key].sort(key=sort_key)
    # Never reconstruct these rows under synthetic All, or fold equal task IDs
    # from different homes. A parent mirror may share the exact owner identity.
    deliveries = {}
    for _home, home_cols, _totals in parts:
        for card in home_cols.get("landed") or []:
            identity = getattr(card, "delivery_identity", "")
            key = (identity, card.task) if identity else (id(card), card.task)
            deliveries.setdefault(key, card)
    def delivered_order(card: Card) -> tuple:
        event = getattr(card, 'event_time', None)
        stamp = 0
        if event is not None:
            stamp = event.epoch or (datetime(event.day.year, event.day.month, event.day.day,
                                             tzinfo=timezone.utc).timestamp() if event.day else 0)
        return stamp, card.task, getattr(card, 'delivery_identity', '')

    cols["landed"] = sorted(deliveries.values(), key=delivered_order, reverse=True)
    if LANDED_LIMIT:
        cols["landed"] = cols["landed"][:LANDED_LIMIT]
    if cols["captains_call"]:
        cols["captains_call"] = _dedupe_captains_call(cols["captains_call"])
        totals["captains_call_hidden"] = sum(bool(getattr(c, "answered_hidden", False)) for c in cols["captains_call"])
        totals["captains_call"] = len(cols["captains_call"]) - totals["captains_call_hidden"]
    return cols, totals


# ----------------------------------------------------------------------------
# home discovery
# ----------------------------------------------------------------------------


class Home:
    def __init__(
        self,
        label: str,
        path: str,
        kind: str,
        mate_id: str = "",
        route: str = "",
        via: str = "",
    ):
        self.label = label
        self.path = path
        self.kind = kind  # "captain" | "secondmate" | "other"
        # The treehouse lease holder (the mate id the snapshot's owner column
        # uses), kept even when a presentation label overrides the display name.
        self.mate_id = mate_id
        # A remote route (data/secondmates.md with host:/root:/home:) is reached
        # through Firstmate's own transport from the captain home, not a local
        # path; ``route`` is the mate id and ``via`` the captain home path.
        self.route = route
        self.via = via

    def __repr__(self) -> str:
        return f"Home({self.label!r}, {self.path!r}, {self.kind!r})"


def _plugin_config_dir() -> str:
    """The plugin config dir Herdr hands plugin processes (matching
    kanban-view.sh), with HERDR_CONFIG_DIR and then Herdr's own XDG config
    root as the fallback for manual runs, the web server and tests."""
    plugin_dir = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if plugin_dir:
        return plugin_dir
    base = os.environ.get("HERDR_CONFIG_DIR") or os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "herdr"
    )
    return os.path.join(base, "plugins", "config", "herdr-firstmate-flow")


def _is_firstmate_home(path: str) -> bool:
    return os.path.isdir(path) and os.path.isfile(endpoint_path(path, "fm-bearings-snapshot.sh"))


def _has_task_dirs(path: str) -> bool:
    data = os.path.join(path, "data")
    try:
        with os.scandir(data) as it:
            for e in it:
                if e.is_dir() and not e.name.startswith("."):
                    return True
                if e.is_file() and e.name.endswith(".md") and not NON_TASK.match(e.name):
                    return True
    except OSError:
        return False
    return False


def _lease_holders(treehouse_root: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for state_file in glob.glob(os.path.join(treehouse_root, "*", "treehouse-state.json")):
        try:
            with open(state_file) as fh:
                data = json.load(fh)
        except Exception:
            continue
        for wt in (data.get("worktrees") or []):
            holder = wt.get("lease_holder")
            path = wt.get("path")
            if holder and path:
                out[os.path.realpath(path)] = holder
    return out


def _presentation_labels() -> dict[str, str]:
    """home path -> parent_label from any .herdr-presentation file in it."""
    out: dict[str, str] = {}
    for state in glob.glob(os.path.expanduser("~/.treehouse/*/*/firstmate/state")):
        home = os.path.dirname(state)
        try:
            names = os.listdir(state)
        except OSError:
            continue
        for name in names[:200]:
            if not name.endswith(".herdr-presentation"):
                continue
            try:
                with open(os.path.join(state, name)) as fh:
                    for line in fh:
                        if line.startswith("parent_label=") and "parent_label" not in out:
                            out[home] = line.split("=", 1)[1].strip()
                            break
                if home in out:
                    break
            except OSError:
                pass
    return out


def _home_label(home: Home, leases: dict[str, str], pres: dict[str, str]) -> str:
    if home.kind == "captain":
        return "captain"
    rel = os.path.realpath(home.path)
    if rel in pres:
        return pres[rel]
    if rel in leases:
        return f"2ndmate-{leases[rel]}"
    return os.path.basename(os.path.dirname(home.path)) or "mate"


def _live_agent_homes() -> set[str]:
    """Set of cwds where a live Herdr agent runs (used to keep fresh mates)."""
    agents, _ = herdr_agents()
    return {os.path.realpath(a.get("cwd", "")) for a in agents.values() if a.get("cwd")}


def remote_routes(captain_path: str) -> list[tuple[str, str]]:
    """(mate id, remote home path) for every remote route in secondmates.md.

    A remote route is a registry line with host:, root: and home: fields; a
    local route carries home: alone. The deck reaches a remote home through
    Firstmate's own fm-on.sh transport, never raw SSH or a local path.
    """
    path = os.path.join(captain_path, "data", "secondmates.md")
    out: list[tuple[str, str]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return out
    for line in lines:
        m = re.match(r"^- (\S+) - ", line)
        if not m or "host:" not in line or "root:" not in line or "home:" not in line:
            continue
        fields = dict(re.findall(r"(host|root|home): ([^;)]+)", line))
        remote_home = (fields.get("home") or "").strip()
        if remote_home:
            out.append((m.group(1), os.path.expanduser(remote_home)))
    return out


def discover_homes(verbose: bool = False) -> list[Home]:
    candidates: list[str] = []

    # explicit config: label=path lines
    cfg = os.path.join(_plugin_config_dir(), "homes.conf")
    explicit: list[tuple[str, str]] = []
    if os.path.isfile(cfg):
        try:
            with open(cfg) as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    label, path = line.split("=", 1)
                    explicit.append((label.strip(), os.path.expanduser(path.strip())))
        except OSError:
            pass

    json_homes = os.environ.get("FM_FLOW_HOMES_JSON", "")
    if json_homes:
        parsed = json.loads(json_homes)
        if not isinstance(parsed, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in parsed.items()):
            raise ValueError('FM_FLOW_HOMES_JSON must map labels to nonempty paths')
        explicit.extend((label, os.path.expanduser(path)) for label, path in parsed.items())
    env_homes = os.environ.get("FM_FLOW_HOMES", "").strip()
    if env_homes:
        for item in env_homes.replace(",", " ").split():
            if "=" in item:
                label, path = item.split("=", 1)
                explicit.append((label.strip(), os.path.expanduser(path.strip())))

    # homes_only pins discovery to the explicit FM_FLOW_HOMES / homes.conf
    # list. A bare homes_only with nothing configured falls back to the scan,
    # so a typo cannot make the board show nothing.
    only_explicit = bool(explicit or json_homes) and homes_only_default()
    if only_explicit and not explicit:
        return []
    single = ""
    treehouse_paths: set[str] = set()
    if not only_explicit:
        single = os.environ.get("FM_HOME", "").strip()
        if not single:
            fm_home_file = os.path.join(_plugin_config_dir(), "fm_home")
            try:
                with open(fm_home_file) as fh:
                    lines = (line.strip() for line in fh)
                    single = next((line for line in lines if line and not line.startswith("#")), "")
            except OSError:
                single = ""
        if single:
            candidates.append(os.path.expanduser(single))

        candidates.append(os.path.expanduser("~/firstmate"))
        treehouse_paths = {
            os.path.realpath(p)
            for p in glob.glob(os.path.expanduser("~/.treehouse/*/*/firstmate"))
        }
        candidates.extend(sorted(treehouse_paths))

    leases = _lease_holders(os.path.expanduser("~/.treehouse"))
    pres = _presentation_labels()
    try:
        live_homes = _live_agent_homes()
    except Exception:
        live_homes = set()

    homes: list[Home] = []
    seen: set[str] = set()

    def add(
        path: str, forced_label: str | None = None, require_assignment: bool = False
    ) -> None:
        path = os.path.realpath(os.path.expanduser(path))
        if path in seen or not _is_firstmate_home(path):
            return
        # An unleased treehouse worktree is a spare slot, not a crew: no mate
        # was assigned to it, so a bare worktree number stays out of the tabs.
        if (
            require_assignment
            and path not in leases
            and path not in pres
            and path not in live_homes
        ):
            return
        seen.add(path)
        has_captain = os.path.isfile(os.path.join(path, "data", "captain.md"))
        kind = "captain" if has_captain else "secondmate"
        configured = bool(forced_label or single and path == os.path.realpath(os.path.expanduser(single)))
        if not configured and not _has_task_dirs(path) and path not in live_homes:
            return
        home = Home("", path, kind, mate_id=leases.get(path, ""))
        home.label = forced_label or _home_label(home, leases, pres)
        homes.append(home)

    for label, path in explicit:
        add(path, forced_label=label)
    configured = {os.path.realpath(os.path.expanduser(p)) for _, p in explicit}
    if single:
        configured.add(os.path.realpath(os.path.expanduser(single)))
    for path in candidates:
        real = os.path.realpath(os.path.expanduser(path))
        add(path, require_assignment=real in treehouse_paths and real not in configured)

    homes.sort(key=lambda h: (h.kind != "captain", h.label))
    # A remote secondmate route lives in the captain home's registry; reach
    # it through fm-on.sh and show it as its own crew tab. A local home with
    # the same label already covers that crew.
    captain = next((h for h in homes if h.kind == "captain"), None)
    if captain is not None:
        labels = {h.label for h in homes}
        for route_id, remote_home in remote_routes(captain.path):
            if route_id in labels:
                continue
            labels.add(route_id)
            homes.append(
                Home(route_id, remote_home, "secondmate", route=route_id, via=captain.path)
            )
    homes = inject_all_crew(homes)
    if verbose:
        for h in homes:
            print(f"{h.label:32s} {h.path}  [{h.kind}]")
    return homes


# ----------------------------------------------------------------------------
# herdr + firstmate data collection
# ----------------------------------------------------------------------------


def _herdr_bin() -> str:
    """Resolve the herdr binary. Plugin panes get a minimal PATH that usually
    lacks ~/.local/bin, so HERDR_BIN_PATH and explicit fallbacks matter."""
    for candidate in (
        os.environ.get("HERDR_BIN_PATH"),
        shutil.which("herdr"),
        os.path.expanduser("~/.local/bin/herdr"),
        "/usr/local/bin/herdr",
    ):
        if candidate and os.path.exists(candidate):
            return candidate
    return "herdr"


HERDR_BIN = _herdr_bin()


def _child_env() -> dict:
    """Environment for child processes: keep the pane env, but make sure the
    usual user bin directories are on PATH (Firstmate scripts and herdr live
    there, while plugin panes inherit a minimal PATH)."""
    env = dict(os.environ)
    parts = [p for p in (env.get("PATH") or "").split(os.pathsep) if p]
    for extra in (os.path.expanduser("~/.local/bin"), "/usr/local/bin"):
        if extra not in parts:
            parts.append(extra)
    env["PATH"] = os.pathsep.join(parts)
    env.setdefault("HERDR_BIN_PATH", HERDR_BIN)
    return env


def _run(cmd: list[str], timeout: float = 25.0) -> str | None:
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_child_env(),
            stdin=subprocess.DEVNULL,
        )
        if p.returncode != 0:
            _debug(f"run failed rc={p.returncode}: {' '.join(cmd[:3])} {p.stderr.strip()[:120]}")
            return None
        return p.stdout
    except Exception as exc:
        _debug(f"run error: {' '.join(cmd[:3])} {exc!r}")
        return None


def _by_id(items, key: str) -> dict[str, dict]:
    return {i[key]: i for i in items or [] if isinstance(i, dict) and i.get(key)}


def _herdr_list(args: list[str], field: str, key: str) -> dict[str, dict]:
    out = _run([HERDR_BIN, *args])
    if not out:
        return {}
    try:
        return _by_id(json.loads(out).get("result", {}).get(field), key)
    except Exception:
        return {}


def herdr_state() -> dict[str, dict[str, dict]]:
    """Live Herdr agents, panes, workspaces and tabs, each keyed by its id.

    One ``herdr api snapshot`` carries all four; when it fails, the agent and
    pane lists are read the old way and the workspace/tab labels stay empty.
    """
    out = _run([HERDR_BIN, "api", "snapshot"])
    if out:
        try:
            snap = json.loads(out)["result"]["snapshot"]
            return {
                "agents": _by_id(snap.get("agents"), "pane_id"),
                "panes": _by_id(snap.get("panes"), "pane_id"),
                "workspaces": _by_id(snap.get("workspaces"), "workspace_id"),
                "tabs": _by_id(snap.get("tabs"), "tab_id"),
            }
        except Exception:
            pass
    return {
        "agents": _herdr_list(["agent", "list"], "agents", "pane_id"),
        "panes": _herdr_list(["pane", "list"], "panes", "pane_id"),
        "workspaces": {},
        "tabs": {},
    }


def herdr_agents() -> tuple[dict[str, dict], dict[str, dict]]:
    """pane_id -> agent info, pane_id -> pane info (always live from Herdr)."""
    state = herdr_state()
    return state["agents"], state["panes"]


# Live run stats (Herdr sidebar-style elapsed + token use), cached per pane.
_AGENT_STATS_TTL = max(1.0, min(30.0, _float(os.environ.get("FM_FLOW_AGENT_STATS_SECS"), TICK_SECS)))
_AGENT_STATS_CACHE: dict[str, tuple[float, str, str]] = {}
_RUN_STATS_HERDR_RE = re.compile(
    r"(?:Herding|Working|Running|Thinking)[^\n]{0,40}?"
    r"\(([^)·]+)\s*·\s*↓?\s*([\d.]+[kKmM]?)\s*tokens?\)",
    re.I,
)
_RUN_STATS_INLINE_RE = re.compile(
    r"(\d+(?:m\s+\d+)?s|\d+h\s+\d+m(?:\s+\d+s)?|\d+m\s+\d+s)\s*·\s*↓?\s*([\d.]+[kKmM]?)\s*tokens?",
    re.I,
)
_PI_FOOTER_TOKENS_RE = re.compile(r"↑[\d.]+[kKmM]?\s*↓([\d.]+[kKmM]?)\b")
_SESSION_TAIL_BYTES = 512_000


def _format_elapsed(seconds: float) -> str:
    try:
        s = int(max(0, seconds))
    except (TypeError, ValueError):
        return ""
    if s >= 3600:
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        if sec:
            return f"{h}h {m}m {sec}s"
        return f"{h}h {m}m"
    if s >= 60:
        m, sec = divmod(s, 60)
        return f"{m}m {sec}s"
    return f"{s}s"


def badge_label_from_badge(badge: str) -> str:
    """Human label from a badge like ``◐ validating`` -> ``validating``."""
    s = (badge or "").strip()
    if " " in s:
        return s.split(None, 1)[1].lower()
    return s.lower()


def _format_token_count(total: int) -> str:
    if total >= 1_000_000:
        text = f"{total / 1_000_000:.1f}M"
    elif total >= 1000:
        text = f"{total / 1000:.1f}k"
    else:
        return str(total)
    return text.replace(".0M", "M").replace(".0k", "k")


def _normalize_token_display(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    return raw if raw.lower().endswith(("k", "m")) else raw


def parse_spawn_gen_epoch(spawn_gen: str) -> float:
    """Epoch seconds from a Firstmate ``spawn_gen`` (``s<unix>.…``)."""
    if not spawn_gen:
        return 0.0
    raw = spawn_gen.strip()
    if raw.startswith("s"):
        raw = raw[1:]
    head = raw.split(".", 1)[0]
    try:
        return float(head)
    except (TypeError, ValueError):
        return 0.0


def _usage_total_tokens(usage: dict) -> int:
    total = _int(usage.get("totalTokens"))
    if total > 0:
        return total
    return (
        _int(usage.get("input"))
        + _int(usage.get("output"))
        + _int(usage.get("cacheRead"))
        + _int(usage.get("cacheWrite"))
    )


def parse_run_stats_from_detection(text: str) -> tuple[str, str]:
    """Return (elapsed, tokens) parsed from a Herdr detection buffer."""
    if not text:
        return "", ""
    for pattern in (_RUN_STATS_HERDR_RE, _RUN_STATS_INLINE_RE):
        m = pattern.search(text)
        if m:
            elapsed = m.group(1).strip()
            tokens = _normalize_token_display(m.group(2))
            return elapsed, tokens
    m = _PI_FOOTER_TOKENS_RE.search(text)
    if m:
        return "", _normalize_token_display(m.group(1))
    return "", ""


def _parse_iso_ts(value: str) -> float:
    if not value:
        return 0.0
    value = value.strip()
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        from datetime import datetime

        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def pi_session_run_stats(session_path: str) -> tuple[str, str]:
    """Total session wall time + cumulative tokens from a Pi session file."""
    if not session_path or not os.path.isfile(session_path):
        return "", ""
    try:
        size = os.path.getsize(session_path)
        with open(session_path, errors="replace") as fh:
            if size > _SESSION_TAIL_BYTES:
                fh.seek(size - _SESSION_TAIL_BYTES)
                fh.readline()
            blob = fh.read()
    except OSError:
        return "", ""
    tokens = 0
    first_ts = 0.0
    for line in blob.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        ts = _parse_iso_ts(str(obj.get("timestamp") or ""))
        if ts and (not first_ts or ts < first_ts):
            first_ts = ts
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        if not ts:
            ts = _parse_iso_ts(str(msg.get("timestamp") or ""))
            if ts and (not first_ts or ts < first_ts):
                first_ts = ts
        usage = msg.get("usage")
        if isinstance(usage, dict):
            tokens += _usage_total_tokens(usage)
    elapsed = _format_elapsed(time.time() - first_ts) if first_ts else ""
    token_text = _format_token_count(tokens) if tokens else ""
    return elapsed, token_text


def _herdr_detection_text(pane_id: str) -> str:
    if not pane_id or not HERDR_BIN:
        return ""
    out = _run(
        [HERDR_BIN, "agent", "read", pane_id, "--source", "detection", "--lines", "30", "--format", "text"],
        timeout=4.0,
    )
    return out or ""


def agent_run_stats(pane_id: str, agent_info: dict | None) -> tuple[str, str]:
    """Best-effort elapsed + token display for one agent pane."""
    if not pane_id:
        return "", ""
    now = time.time()
    cached = _AGENT_STATS_CACHE.get(pane_id)
    if cached and now - cached[0] < _AGENT_STATS_TTL:
        return cached[1], cached[2]

    elapsed, tokens = "", ""
    detection = _herdr_detection_text(pane_id)
    elapsed, tokens = parse_run_stats_from_detection(detection)

    if not tokens or not elapsed:
        session = (agent_info or {}).get("agent_session") or {}
        session_path = session.get("value") if isinstance(session, dict) else ""
        agent_kind = (agent_info or {}).get("agent") or session.get("agent") or ""
        if agent_kind == "pi" and session_path:
            pi_elapsed, pi_tokens = pi_session_run_stats(str(session_path))
            elapsed = elapsed or pi_elapsed
            tokens = tokens or pi_tokens

    _AGENT_STATS_CACHE[pane_id] = (now, elapsed, tokens)
    return elapsed, tokens


def enrich_cards_run_stats(cols: dict[str, list[Card]], agents: dict[str, dict]) -> None:
    """Attach live run stats to in-flight cards that map to a working agent pane."""
    pane_stats: dict[str, tuple[str, str]] = {}
    for cards in cols.values():
        for card in cards:
            if (
                card.bucket not in ("landed", "captains_call")
                and card.pane_id
                and card.live_status in ("working", "blocked")
                and card.pane_id not in pane_stats
            ):
                pane_stats[card.pane_id] = agent_run_stats(card.pane_id, agents.get(card.pane_id))
    now = time.time()
    for cards in cols.values():
        for card in cards:
            if card.bucket != "captains_call" and card.pane_id and card.live_status in ("working", "blocked"):
                elapsed, tokens = pane_stats.get(card.pane_id, ("", ""))
                spawn_epoch = getattr(card, "spawn_epoch", 0.0) or 0.0
                if spawn_epoch > 0:
                    elapsed = _format_elapsed(now - spawn_epoch)
                card.run_elapsed, card.run_tokens = elapsed, tokens
            else:
                card.run_elapsed, card.run_tokens = "", ""


_META_CACHE: dict[str, tuple[float, dict]] = {}


def read_meta(path: str) -> dict:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    cached = _META_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    meta: dict[str, str] = {}
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                if "=" in line and not line.startswith("#"):
                    k, v = line.rstrip("\n").split("=", 1)
                    meta[k.strip()] = v.strip()
    except OSError:
        pass
    _META_CACHE[path] = (mtime, meta)
    return meta


def _status_lines(path: str) -> list[str]:
    """Read the same bounded tail for inspection and task-report selection."""
    try:
        size = os.path.getsize(path)
        with open(path, errors="replace") as fh:
            if size > 8192:
                fh.seek(size - 8192)
                fh.readline()
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    except OSError:
        return []
    return lines


def read_status_tail(path: str) -> tuple[str, str, int]:
    """Return the original last status text, including supervision metadata."""
    lines = _status_lines(path)
    if not lines:
        return "", "", 0
    line = lines[-1]
    m = re.match(r"^(?P<state>[a-zA-Z_-]+)(?:\s+\[at=(?P<at>\d+)\])?\s*:?\s*(?P<text>.*)$", line)
    if not m:
        return "", line[:200], 0
    at = re.search(r"\[at=(\d+)\]", line)
    return m.group("state"), m.group("text")[:200], _int(at.group(1)) if at else 0


def task_report_text(text: str) -> str:
    """Accept task prose, not delivery receipts, routing or machine records.

    Only the record envelope is removed. A control message is rejected as a
    whole; trimming one key from it would still leave meaningless progress.
    The original log and read_status_tail remain available to supervision.
    """
    text = re.sub(r"^(?:\s*\[(?:at|key|resolve-key)=[^\]]*\]\s*)+:?\s*", "", text).strip()
    if not text or text in ("-", "captain-hold"):
        return ""
    if re.search(
        r"\[(?:key|at|resolve-key)=[^\]]*\]|\bresolve[-_ ]key\b|"
        r"\bw[A-Za-z0-9]+:[pt]\d+\b|\bp:[A-Za-z0-9_-]{22}\b|"
        r"\b(?:endpoint_task_id|spawn_gen|agent_session)\s*[:=]|"
        r"\b(?:inbox|acknowledged|acknowledgement|delivery receipt)\b|"
        r"\b(?:root|firstmate)\s*(?:routing|route|->)|/root/|"
        r"\b(?:routed|forwarded|delivered)\s+(?:to\s+)?(?:root|firstmate|inbox)\b",
        text, re.IGNORECASE,
    ):
        return ""
    if text.lower().startswith(("harness state unavailable", "harness busy", "answered:", "resolved:")):
        return ""
    if re.match(r'^(heartbeat(?:\s+poll)?|poll|liveness)\b', text, re.I):
        return ''
    return text


def read_task_report(path: str, spawn_epoch: float = 0, *, durable: bool = False) -> tuple[str, str, int]:
    """Select a task declaration, preserving its age and resolution boundary."""
    for line in reversed(_status_lines(path)):
        match = re.match(
            r"^([a-zA-Z_-]+)((?:\s+\[(?:at|key|resolve-key)=[^\]]*\])*)\s*:\s*(.*)$",
            line,
        )
        if not match:
            continue
        state, envelope, text = match.groups()
        lead = re.match(r"^((?:\[(?:at|key|resolve-key)=[^\]]*\]\s*)+):?\s*", text)
        if lead:
            envelope += lead.group(1)
            text = text[lead.end():]
        # A resolution invalidates earlier waiting/outcome messages. Do not
        # resurrect a resolved blocker while looking for less technical prose.
        if state == "resolved":
            break
        if state not in ("working", "paused", "parked", "blocked", "failed", "done", "needs-decision"):
            continue
        at = re.search(r"\[at=(\d+)\]", envelope)
        epoch = _int(at.group(1)) if at else 0
        if spawn_epoch and epoch and epoch < spawn_epoch:
            break
        # Waits/outcomes are durable declarations even when they mention an
        # inbox or routing receipt. Only working prose can imply progress, so
        # retain its stricter filter. Never interpret the prose as a phase.
        text = text.strip() if durable and state != 'working' else task_report_text(text)
        if text:
            return state, text, epoch
    return "", "", 0


def read_open_blockers(path: str, spawn_epoch: float = 0, kind: str = "") -> list[dict]:
    """Blocker declarations in the bounded log, folded with task terminal rules.

    A coordination receipt is not progress, but it must not hide the reason
    inside a blocked declaration. Keep that full payload separate from reports.
    Unkeyed declarations describe the latest phase; keyed blockers survive a
    later working record until an exact resolved key arrives. Ship/scout done
    or failed supersedes all earlier declarations; secondmate terminals do not.
    """
    opened: dict[str, dict] = {}
    for line in _status_lines(path):
        match = re.match(r"^([a-zA-Z_-]+)\s*((?:\[[^\]]*\]\s*)*):\s*(.*)$", line)
        if not match:
            continue
        state, envelope, text = match.groups()
        # Older producers put metadata immediately after the colon.
        lead = re.match(r"^((?:\[(?:at|key|resolve-key)=[^\]]*\]\s*)+)", text)
        if lead:
            envelope += lead.group(0)
            text = text[lead.end():]
        at = re.search(r"\[at=(\d+)\]", envelope)
        epoch = _int(at.group(1)) if at else 0
        if spawn_epoch and epoch and epoch < spawn_epoch:
            continue
        key_match = re.search(r"\[(?:key|resolve-key)=([^\]]+)\]", envelope)
        key = key_match.group(1) if key_match else ""
        if state in ("done", "failed") and kind in ("ship", "scout"):
            opened.clear()
        elif state == "resolved":
            opened.pop(key, None)
        elif state in ("blocked", "needs-decision"):
            opened[key] = {"state": state, "key": key, "text": text.strip(), "at": epoch}
        elif not key and state in ("working", "paused", "parked", "done", "failed"):
            opened.pop("", None)
    return list(opened.values())


def build_meta_index(homes: list[Home]) -> dict[str, dict[str, str]]:
    """home path -> task id -> state/<task>.meta path across all homes."""
    index: dict[str, dict[str, str]] = {}
    for home in homes:
        state = os.path.join(home.path, "state")
        try:
            names = os.listdir(state)
        except OSError:
            continue
        tasks = index.setdefault(home.path, {})
        for name in names:
            if name.endswith(".meta"):
                task = name[: -len(".meta")]
                tasks.setdefault(task, os.path.join(state, name))
    return index


def _store_key(path: str) -> str:
    return os.path.normpath(os.path.expanduser(path.strip()))


def _is_store_path(value: str) -> bool:
    return os.path.isabs(value) or value.startswith("~")


_ACCOUNT_CACHE: dict[str, tuple[float, dict[str, str]]] = {}


def _read_account_names(path: str) -> dict[str, str]:
    """store path -> account name from one crew-accounts.json, cached by mtime."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    cached = _ACCOUNT_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    names: dict[str, str] = {}
    try:
        with open(path, errors="replace") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = None
    entries = data.get("accounts") if isinstance(data, dict) else None
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            for key in ("codex_home", "claude_config_dir"):
                store = str(entry.get(key) or "").strip()
                if store:
                    names.setdefault(_store_key(store), name)
    _ACCOUNT_CACHE[path] = (mtime, names)
    return names


def load_account_names(homes: list[Home]) -> dict[str, dict[str, str]]:
    """home path -> store path -> account name from its crew-accounts.json.

    Firstmate's account file is host-local and optional; a home without one
    simply contributes no names and the card keeps its harness label.
    """
    names: dict[str, dict[str, str]] = {}
    for home in homes:
        if not home.path:
            continue
        path = os.path.join(home.path, "config", "crew-accounts.json")
        names[home.path] = _read_account_names(path)
    return names


def account_label(meta: dict, store_names: dict[str, str]) -> str:
    """The account running a ticket: ``claude-b``, ``codex-a``, ``deepseek``.

    A worker meta records the account name directly (``account=``) or a store
    path; a secondmate meta pins the name per vendor (``account_<vendor>``).
    The store path resolves through the worker's own vendor field in each home's
    ``crew-accounts.json``. A pi worker has no account name, so its provider
    stands in. A meta that names no account returns an empty string and the card
    keeps the harness label.
    """
    harness = (meta.get("harness") or "").strip()
    vendor = "pi" if harness == "pi-signed" else harness
    keys = ("account", f"account_{vendor}")
    for key in keys:
        value = (meta.get(key) or "").strip()
        if value and value != "ordinary" and not _is_store_path(value):
            return value
    store_keys = list(keys)
    store_field = {"claude": "claude_config_dir", "codex": "codex_home"}.get(vendor)
    if store_field:
        store_keys.append(store_field)
    for key in store_keys:
        value = (meta.get(key) or "").strip()
        if value:
            name = store_names.get(_store_key(value))
            if name:
                return name
    if vendor == "pi":
        model = (meta.get("model") or "").strip()
        provider = (meta.get("account_provider") or "").strip()
        if not provider and "/" in model:
            provider = model.split("/", 1)[0].strip()
        if provider:
            return provider
    return ""


def bearings_flags() -> list[str]:
    """Follow Firstmate's own bounds (in_flight/gates 20, landed 6).
    Landed ties are ordered by task ID, not inferred intra-day recency.
    FM_FLOW_ALL=1 asks for every row instead; H reads retained history.
    endpoints is Firstmate's own backend presence probe, not model activity.
    """
    flags = ["--json", "--fields", "paths,endpoints"]
    if _on("FM_FLOW_ALL"):
        flags += ["--all-in-flight", "--all-queued", "--all-landed"]
    return flags


def remote_bearings_snapshot(home: Home) -> dict:
    """A remote home's bearings through Firstmate's own transport (fm-on.sh).

    A failure returns {} so the caller keeps the last good snapshot: a remote
    host can be offline for a long time and the board must never block on it.
    """
    if not home.route or not home.via:
        return {}
    fm_on = endpoint_path(home.via, "fm-on.sh")
    if not os.path.isfile(fm_on):
        return {}
    env = _child_env()
    env["FM_HOME"] = home.via
    try:
        p = subprocess.run(
            [fm_on, home.route, "fm-bearings-snapshot.sh", *bearings_flags()],
            capture_output=True,
            text=True,
            timeout=_bearings_timeout(),
            env=env,
            stdin=subprocess.DEVNULL,
        )
        if p.returncode != 0:
            return {}
        return json.loads(p.stdout)
    except Exception:
        return {}


def bearings_snapshot(home: Home) -> dict:
    if home.route:
        return remote_bearings_snapshot(home)
    bin_path = endpoint_path(home.path, "fm-bearings-snapshot.sh")
    env = _child_env()
    env["FM_HOME"] = home.path
    try:
        p = subprocess.run(
            [bin_path, *bearings_flags()],
            capture_output=True,
            text=True,
            timeout=_bearings_timeout(),
            env=env,
            stdin=subprocess.DEVNULL,
        )
        if p.returncode != 0:
            return {}
        return json.loads(p.stdout)
    except Exception:
        return {}


def _strip_owner(task_id: str) -> str:
    return task_id.split("/", 1)[1] if "/" in task_id else task_id


# ----------------------------------------------------------------------------
# Captain's Call decision cards
# ----------------------------------------------------------------------------

_BOARD_CARD_CACHE: dict[str, tuple[tuple, dict[str, Any]]] = {}


def _cards_from_json(data: object) -> dict[str, dict]:
    """key -> card for the captains_call of a board payload."""
    out: dict[str, dict] = {}
    if not isinstance(data, dict):
        return out
    for item in data.get("captains_call") or []:
        if isinstance(item, dict) and isinstance(item.get("key"), str) and item["key"]:
            out[item["key"]] = item
    return out


def _cached_cards(path: str, extract) -> dict[str, dict]:
    """Parse one card source, cached by mtime+size so a 2s tick never
    re-parses a board file that has not changed."""
    try:
        st = os.stat(path)
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    hit = _BOARD_CARD_CACHE.get(path)
    if hit is not None and hit[0] == sig:
        return hit[1]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        cards = extract(text)
    except Exception as exc:
        _debug(f"decision card read failed for {path}: {exc!r}")
        cards = {}
    _BOARD_CARD_CACHE[path] = (sig, cards)
    return cards


def _board_file_cards(path: str) -> dict[str, dict]:
    """The captains_call payload of a live Lavish board (the board file is the
    post-build artifact, so it carries the injected reconcile option)."""

    def extract(text: str) -> dict[str, dict]:
        m = re.search(
            r'<script[^>]*\bid="bearings-data"[^>]*>(.*?)</script>', text, re.S
        )
        if not m:
            return {}
        try:
            return _cards_from_json(json.loads(m.group(1)))
        except (ValueError, TypeError):
            return {}

    return _cached_cards(path, extract)


def _payload_file_cards(path: str) -> dict[str, dict]:
    """A composed data/bearings-board-payload-*.json (pre-build: no reconcile
    option, so the dialog injects the standard one itself)."""

    def extract(text: str) -> dict[str, dict]:
        try:
            return _cards_from_json(json.loads(text))
        except (ValueError, TypeError):
            return {}

    return _cached_cards(path, extract)


def _store_entry(path: str) -> tuple[dict, str]:
    """One durable state/decision-cards/<task>.json record (schema
    fm-decision-card.v1): the card and the generated stamp beside it, cached by
    mtime+size like the other card sources. The stamp says when the board last
    composed the card, and the answered-call ledger records it."""
    try:
        st = os.stat(path)
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}, ""
    hit = _BOARD_CARD_CACHE.get(path)
    if hit is not None and hit[0] == sig:
        cached = hit[1]
        cached_card = cached.get("__card__")
        cached_stamp = cached.get("__stamp__")
        if isinstance(cached_card, dict) and isinstance(cached_stamp, str):
            return cached_card, cached_stamp
    card: dict = {}
    stamp = ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = json.loads(fh.read())
        if isinstance(data, dict):
            inner = data.get("card")
            if isinstance(inner, dict):
                card = inner
            stamp = str(data.get("generated") or "")
    except Exception as exc:
        _debug(f"decision card store read failed for {path}: {exc!r}")
        card, stamp = {}, ""
    _BOARD_CARD_CACHE[path] = (sig, {"__card__": card, "__stamp__": stamp})
    return card, stamp


def _store_card(path: str) -> dict:
    """The card half of :func:`_store_entry`."""
    return _store_entry(path)[0]


def decision_card_record(key: str, home: Home | None) -> tuple[dict, str]:
    """The composed card for one captain-held task id, and the generated stamp
    of the store record it came from. A card read from a board file or a
    payload carries no stamp of its own, so it answers "".

    The durable decision-card store is AUTHORITATIVE: a raise-time card is
    written there the moment the call is raised (bin/fm-captain-hold.sh), and
    the board build persists the effective card there too, so a stored record is
    never older than the board page it came from. The live board file is the next
    source, and the composed payload files it was built from are the last
    fallback. All sources belong to the supplied home.
    """
    # every card source is keyed by a validated slug; never build a path from
    # anything else
    if home is None or not KEY_RE.fullmatch(key):
        return {}, ""
    card, stamp = _store_entry(
        os.path.join(home.path, "state", "decision-cards", f"{key}.json")
    )
    if card:
        return card, stamp
    card = _board_file_cards(
        os.path.join(home.path, ".lavish", "bearings-board.html")
    ).get(key)
    if card:
        return card, ""
    for path in sorted(
        glob.glob(os.path.join(home.path, "data", "bearings-board-payload-*.json")),
        reverse=True,
    ):
        card = _payload_file_cards(path).get(key)
        if card:
            return card, ""
    return {}, ""


def decision_card_for(key: str, home: Home | None) -> dict:
    """The composed card for one captain-held task id."""
    return decision_card_record(key, home)[0]


def resolve_owner_home(
    homes: list[Home] | None, owner: str, active: Home | None
) -> Home | None:
    """The home whose backlog owns a Captain's Call row.

    The snapshot marks a home's own holds ``(main)`` and a secondmate's with
    the mate id, which the discovered crew labels carry (``2ndmate-<id>``).
    """
    if not owner or owner == "(main)":
        return active
    for home in homes or []:
        if home.mate_id == owner:
            return home
    for home in homes or []:
        if home.label == owner:
            return home
    for home in homes or []:
        if home.label.endswith("-" + owner) or os.path.basename(home.path) == owner:
            return home
    return None


def resolve_owner_lane(homes: list[Home] | None, owner: str) -> tuple[Home | None, str]:
    """The parent home and lane id that can wake a mate-owned call's agent.

    The parent home is the one whose state directory records the mate lane
    (state/<owner>.meta); a home-owned ``(main)`` row has no lane to steer.
    """
    if not owner or owner == "(main)" or not KEY_RE.fullmatch(owner):
        return None, ""
    for home in homes or []:
        if os.path.isfile(os.path.join(home.path, "state", f"{owner}.meta")):
            return home, owner
    return None, ""


def _call_owner_identities(
    task: str, source: Home, snapshot: dict, homes: list[Home]
) -> set[str]:
    identities = set()
    for row in snapshot.get("decisions_open") or []:
        if _strip_owner(row.get("id") or row.get("key") or "") != task:
            continue
        owner = row.get("owner") or ""
        if not owner or owner == "(main)":
            identities.add(delivery_home_identity(source))
        else:
            matches = {delivery_home_identity(h) for h in homes
                       if resolve_owner_home([h], owner, None) is not None}
            identities.update(matches if len(matches) == 1 else {""})
    return identities


def _bearing_evidence(bearings: dict[str, tuple[dict, float]] | None, home: Home) -> dict:
    data, received_at = (bearings or {}).get(delivery_home_identity(home), ({}, 0.0))
    if received_at <= 0 or time.time() - received_at > _bearings_ttl():
        return {}
    return data


def _legacy_answer_unambiguous(
    task: str, homes: list[Home], home_path: str, bearings: dict[str, tuple[dict, float]]
) -> bool:
    identities = set()
    for source in real_homes(homes):
        data = _bearing_evidence(bearings, source)
        if not data:
            return False
        identities.update(_call_owner_identities(task, source, data, homes))
    return identities == {os.path.realpath(home_path)}


def answer_home_path(
    card: Card, homes: list[Home] | None, active: Home | None
) -> str:
    """The home whose ``fm-captain-hold.sh`` should intake this call's answer."""
    if card.home_path:
        return card.home_path
    owner = (card.owner or "").strip()
    owner_home = resolve_owner_home(homes, owner, active)
    if owner_home is not None and owner_home.path and not is_aggregate_home(owner_home):
        return owner_home.path
    return ""


def _presentation_text(message, legacy=None, **values):
    """Default shared wording; web supplies a catalog without changing TUI text."""
    text = legacy if legacy is not None else message
    return text.format_map(values) if values else text


def decision_card_content(
    card: Card, homes: list[Home] | None, active: Home | None, *, translate=None,
    bearings: dict[str, tuple[dict, float]] | None = None,
) -> dict:
    """Normalized dialog content for a Captain's Call ticket.

    With no composed card (no board yet, or a card not composed for this key),
    the ticket's durable title and hold reason become the title and about line,
    and the captain answers in their own words - or sends Reconcile to have the
    call re-checked.
    """
    tr = translate or _presentation_text
    home_path = answer_home_path(card, homes, active)
    home_list = real_homes(list(homes or []))
    if active is not None and not is_aggregate_home(active) and active not in home_list:
        home_list.append(active)
    owner_home = next((h for h in home_list if _same_path(h.path, home_path) and not is_remote_home(h)), None)
    raw, stamp = decision_card_record(card.task, owner_home)
    if not raw and owner_home is not None:
        for source in home_list:
            if is_remote_home(source) or _same_path(source.path, home_path):
                continue
            evidence = _bearing_evidence(bearings, source)
            if _call_owner_identities(card.task, source, evidence, home_list) != {delivery_home_identity(owner_home)}:
                continue
            raw, stamp = decision_card_record(card.task, source)
            if raw:
                break
    ctype = (str(raw.get("type") or "decision").strip() or "decision")
    options: list[dict[str, str]] = []
    for opt in raw.get("options") or []:
        if not isinstance(opt, dict):
            continue
        value = opt.get("value")
        if not isinstance(value, str) or not _flatten_field(value):
            continue
        value = _flatten_field(value)
        options.append(
            {
                "value": value,
                "label": _flatten_field(opt.get("label")) or value,
                "hint": _flatten_field(opt.get("hint")),
            }
        )
    # The board's build injects the standard reconcile choice on every decision
    # card; do the same for a raw payload or a card-less ticket.
    if ctype == "decision" and all(o["value"] != "reconcile" for o in options):
        options.append({k: tr(v) if k in ('label', 'hint') else v for k, v in RECONCILE_OPTION.items()})
    recommend = raw.get("recommend_value")
    repo = str(raw.get("repo") or "")
    if not repo and card.owner and card.owner != "(main)":
        repo = card.owner
    title = str(raw.get("title") or "")
    about = str(raw.get("about") or "")
    decide = str(raw.get("decide") or "")
    wake_home, wake_lane = resolve_owner_lane(homes, card.owner)
    if not raw:
        # No composed card. Prefer the snapshot's durable title and hold reason;
        # an older snapshot only carries the collapsed "<title>: <reason>"
        # summary, which is split as a fallback.
        summary = str(card.title or "").strip()
        reason = str(card.doing or "").strip()
        if reason in ("", "captain-hold", "decision", "hold"):
            reason = ""
        if reason:
            title, about = summary, reason
        else:
            head, sep, tail = summary.partition(": ")
            if sep and head:
                title, about = head, tail
            else:
                title, about = summary, ""
        title = title or card.task
        # a truncated reason stub is noise, not context
        if about.endswith("\u2026") and len(about) < 24:
            about = ""
        decide = tr('Answer in your own words, or send Reconcile to re-check the latest state.')
    elif not title:
        title = str(card.title or card.task)
    if not decide:
        decide = tr('Pick an option below, or answer in your own words.')
    return {
        "key": card.task,
        "title": _flatten_field(title),
        "type": ctype,
        "repo": _flatten_field(repo),
        "about": _flatten_field(about),
        "decide": _flatten_field(decide),
        "detail": _flatten_field(raw.get("detail")),
        "options": options,
        "recommend": recommend if isinstance(recommend, str) else "",
        "freeform": bool(raw.get("allow_freeform")) if raw else True,
        "freeform_hint": _flatten_field(
            raw.get("freeform_hint") or tr('or answer in your own words…')
        ),
        "close": raw.get("close") if raw.get("close") in ("done", "release") else "",
        "home_path": home_path,
        "wake_home": wake_home.path if wake_home is not None else "",
        "wake_lane": wake_lane,
        "wake_enabled": wake_owner_default(),
        "intake_wakes": intake_wakes_owner(home_path),
        "intake_reconcile_wakes": intake_wakes_owner(home_path, reconcile=True),
        "stamp": stamp,
    }


class DecisionDialog:
    """One Captain's Call card open for a decision (the board card as a modal)."""

    __slots__ = (
        "key",
        "title",
        "ctype",
        "repo",
        "about",
        "decide",
        "detail",
        "options",
        "recommend",
        "freeform",
        "freeform_hint",
        "close",
        "home_path",
        "wake_home",
        "wake_lane",
        "wake_enabled",
        "intake_wakes",
        "intake_reconcile_wakes",
        "digest",
        "stamp",
        "cursor",
        "selected",
        "note",
        "focus",
        "busy",
        "queued",
        "error",
        "done_at",
    )

    def __init__(self, content: dict) -> None:
        self.key = content["key"]
        self.title = content["title"]
        self.ctype = content["type"]
        self.repo = content["repo"]
        self.about = content["about"]
        self.decide = content["decide"]
        self.detail = content["detail"]
        self.options = content["options"]
        self.recommend = content["recommend"]
        self.freeform = content["freeform"]
        self.freeform_hint = content["freeform_hint"]
        self.close = content["close"]
        self.home_path = content["home_path"]
        self.wake_home = content["wake_home"]
        self.wake_lane = content["wake_lane"]
        # Captured when the card opened so the footer can say what will happen;
        # the submit itself re-reads the live setting and the live intake.
        self.wake_enabled = bool(content.get("wake_enabled", True))
        self.intake_wakes = bool(content.get("intake_wakes", False))
        self.intake_reconcile_wakes = bool(content.get("intake_reconcile_wakes", False))
        # What the captain is answering, for the answered-call ledger.
        self.stamp = str(content.get("stamp") or "")
        self.digest = call_digest(self.key, self.title, self.options, self.close)
        self.cursor = 0
        # The board preselects the recommended option; without one the captain
        # starts on the freeform row.
        self.selected = next(
            (
                i
                for i, opt in enumerate(self.options)
                if self.recommend and opt["value"] == self.recommend
            ),
            -1,
        )
        self.note = ""
        self.focus = "options"
        self.busy = False
        self.queued = False
        self.error = ""
        self.done_at = 0.0

    def selected_option(self) -> dict[str, str] | None:
        if 0 <= self.selected < len(self.options):
            return self.options[self.selected]
        return None

    def display_answer(self) -> str:
        """What the captain chose, exactly as the board shows it: the selected
        option plus a typed note, or just the note when no option is selected."""
        option = self.selected_option()
        value = option["value"] if option else ""
        note = self.note.strip()
        if value and note:
            return value + " - " + note
        return value or note

    def keyed_line(self) -> str:
        """The keyed-answer line: key, answer, label, optional card close mode.

        The board's captured prompt carries the note beside the selection; this
        channel has only the keyed line, so the note rides the answer and the
        captain's full words reach the durable decision.
        """
        answer = _flatten_field(self.display_answer())
        line = (
            f"{_flatten_field(self.key)}\t{answer}\t"
            f"{_flatten_field(self.title)} -> {answer}"
        )
        if self.close:
            line += f"\t{self.close}"
        return line

    def reconcile_line(self) -> str:
        """A reconcile request row: the task id and the note as provenance."""
        note = _flatten_field(self.note)
        key = _flatten_field(self.key)
        return f"{key}\t{note}" if note else key


def _hold_script(home_path: str) -> str:
    return endpoint_path(home_path, "fm-captain-hold.sh")


# The step a keyed-answer intake runs to wake the home that owns an answered
# call (Firstmate's settle_answered_call); an intake that defines it notifies
# that home once per answer on its own.
INTAKE_WAKE_RE = re.compile(r"^[ \t]*wake_answer_owner[ \t]*\(\)", re.M)


INTAKE_RECONCILE_WAKE_RE = re.compile(r"^[ \t]*wake_reconcile_owner[ \t]*\(\)", re.M)


def intake_wakes_owner(home_path: str, *, reconcile: bool = False) -> bool:
    """Whether this home's intake wakes the owner for the selected action.

    Such an intake already notifies the owner itself, so a Deck steer on top
    would be a second notification for one submission. For an
    older or unreadable intake, the Deck can instead steer a secondmate-owned
    call's lane. A main-home-owned call has no Deck lane steer.
    Reconcile capability is independent of normal-answer capability.
    """
    if not home_path:
        return False
    try:
        with open(_hold_script(home_path), encoding="utf-8", errors="replace") as fh:
            pattern = INTAKE_RECONCILE_WAKE_RE if reconcile else INTAKE_WAKE_RE
            return bool(pattern.search(fh.read()))
    except OSError:
        return False


def _run_captain_hold(home_path: str, args: list[str], stdin_text: str) -> tuple[bool, str]:
    """Run the firstmate answer intake in the home that owns the task."""
    script = _hold_script(home_path)
    if not os.path.isfile(script):
        return False, f"no fm-captain-hold.sh in {home_path}"
    env = _child_env()
    env["FM_HOME"] = home_path
    try:
        p = subprocess.run(
            [script, *args],
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
        )
    except Exception as exc:
        _debug(f"captain hold failed: {exc!r}")
        return False, f"could not run the answer intake: {exc!r}"
    lines = [ln for ln in ((p.stdout or "") + (p.stderr or "")).splitlines() if ln.strip()]
    detail = lines[-1].strip() if lines else ""
    return p.returncode == 0, detail


def ensure_decision_binding(home_path: str) -> tuple[bool, str]:
    """Reconcile requests need a bound captured source. Binding is a deliberate
    opt-in record the channel makes on its first reconcile, and `bind` does not
    require the source to exist anywhere else."""
    script = _hold_script(home_path)
    if not os.path.isfile(script):
        return False, f"no fm-captain-hold.sh in {home_path}"
    env = _child_env()
    env["FM_HOME"] = home_path
    try:
        p = subprocess.run(
            [script, "binding", DECISION_SOURCE_ID],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            stdin=subprocess.DEVNULL,
        )
        if p.returncode == 0 and (p.stdout or "").strip():
            return True, ""
    except Exception as exc:
        return False, f"could not read the decision binding: {exc!r}"
    return _run_captain_hold(home_path, ["bind", DECISION_SOURCE_ID], "")


def wake_owner(dialog: DecisionDialog) -> str:
    """Steer a secondmate-owned agent through the parent home's lane inbox.

    A main-home-owned call has no lane to steer. Returns an empty string on
    success (or when there is no lane to wake) and a one-line warning the
    dialog can show otherwise.
    """
    if not dialog.wake_home or not dialog.wake_lane:
        return ""
    if not KEY_RE.fullmatch(dialog.wake_lane):
        _debug(f"wake lane refused: {dialog.wake_lane!r}")
        return "owner wake skipped: the lane id is malformed"
    script = endpoint_path(dialog.wake_home, "fm-send.sh")
    if not os.path.isfile(script):
        return f"wake skipped: no fm-send.sh in {dialog.wake_home}"
    option = dialog.selected_option()
    if option and option["value"] == "reconcile":
        text = (
            f"captain's deck reconcile request for {dialog.key}: the captain sent this call "
            "back for a re-check. Run bin/fm-captain-hold.sh reconcile list, then retire it "
            "with reconcile close --evidence-file when the premise is moot, or reconcile note "
            "--note-file when the call is still active. Reconcile is not the captain's words: "
            "never use answer for it."
        )
    else:
        text = (
            f"captain's deck answer for {dialog.key}: {dialog.display_answer()}. The keyed-answer "
            "intake already recorded it; act on it - resume released work, or confirm the call "
            "is closed."
        )
    delivery = hashlib.sha256(f"{dialog.key}\t{text}".encode("utf-8")).hexdigest()[:16]
    env = _child_env()
    env["FM_HOME"] = dialog.wake_home
    try:
        p = subprocess.run(
            [script, dialog.wake_lane, "--fire-and-forget", delivery, text],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    except Exception as exc:
        _debug(f"owner wake failed: {exc!r}")
        return f"answer recorded, but the owner wake failed: {exc!r}"
    if p.returncode == 0:
        return ""
    lines = [ln for ln in ((p.stderr or "") + (p.stdout or "")).splitlines() if ln.strip()]
    detail = lines[-1].strip() if lines else "fm-send refused"
    _debug(f"owner wake refused: {detail}")
    return f"answer recorded, but the owner was not woken: {detail}"


def run_submit(dialog: DecisionDialog) -> tuple[bool, str]:
    """Feed the captain's choice to firstmate's intake without duplicating its
    owner wake, with a secondmate lane steer when the Deck supplies the wake.
    Reconcile takes the separate reconcile-request path; everything else is a
    keyed answer."""
    if not dialog.home_path:
        return False, (
            f"cannot tell which home owns {dialog.key}; "
            "answer it from the Lavish board or chat"
        )
    if not KEY_RE.fullmatch(dialog.key):
        return False, (
            f"refusing a malformed task key: {_flatten_field(dialog.key)[:80]!r}"
        )
    option = dialog.selected_option()
    reconcile = bool(option and option["value"] == "reconcile")
    if reconcile:
        ok, detail = ensure_decision_binding(dialog.home_path)
        if not ok:
            return False, detail
        ok, detail = _run_captain_hold(
            dialog.home_path,
            [
                "reconcile-requests",
                "--source-id",
                DECISION_SOURCE_ID,
                "--source",
                DECISION_SOURCE,
            ],
            dialog.reconcile_line() + "\n",
        )
    else:
        ok, detail = _run_captain_hold(
            dialog.home_path,
            ["answers", "--source", DECISION_SOURCE],
            dialog.keyed_line() + "\n",
        )
        if ok:
            # The row leaves Captain's Call until its owner takes the work:
            # make_cards hides it while this record still matches the row.
            record_answered_call(dialog)
    # An intake that settles answers has already woken the owner of this one
    # (its own wake problem rides the intake's detail), so the Deck only tries
    # its secondmate lane steer when that action has no intake-owned wake.
    # A main-home-owned call has no lane to steer.
    deck_wakes = not intake_wakes_owner(dialog.home_path, reconcile=reconcile)
    if ok and deck_wakes and wake_owner_default():
        # A wake failure never reverses the recorded answer, so it is reported
        # beside the queued state instead of failing the submit.
        wake_detail = wake_owner(dialog)
        if wake_detail:
            detail = f"{detail} | {wake_detail}" if detail else wake_detail
    return ok, detail


# ----------------------------------------------------------------------------
# Message Firstmate (`m`)
# ----------------------------------------------------------------------------

# The longest message the box sends; a longer one is refused with this limit.
MESSAGE_LIMIT = 2000


class MessageBox:
    """The `m` box: what the captain typed and the last refusal."""

    __slots__ = ("text", "error")

    def __init__(self) -> None:
        self.text = ""
        self.error = ""


def supervisor_target(
    home: Home | None, homes: list[Home], agents: dict[str, dict]
) -> tuple[Home | None, str]:
    """(home, pane_id) of the Firstmate supervisor a message from this tab reaches.

    A crew tab targets its own home and the All tab the captain home, which
    home discovery sorts first. The supervisor is the agent whose cwd is the
    home path, the same match the crew activity dot uses.
    """
    target = next(iter(real_homes(homes)), None) if is_aggregate_home(home) else home
    if target is None or not target.path:
        return target, ""
    want = os.path.realpath(target.path)
    for pid in sorted(agents):
        agent = agents[pid]
        cwds = {os.path.realpath(agent[k]) for k in ("cwd", "foreground_cwd") if agent.get(k)}
        if want in cwds:
            return target, pid
    return target, ""


def send_to_firstmate(pane_id: str, text: str) -> tuple[bool, str]:
    """Submit one message to a supervisor pane, as if the captain typed it.

    Returns (ok, detail). On a refusal the detail is herdr's own
    ``code: message``, e.g. ``agent_blocked`` while Firstmate waits at a dialog.
    """
    try:
        p = subprocess.run(
            [HERDR_BIN, "agent", "prompt", pane_id, text],
            capture_output=True,
            text=True,
            timeout=30,
            env=_child_env(),
            stdin=subprocess.DEVNULL,
        )
    except Exception as exc:
        return False, f"could not run herdr agent prompt: {exc!r}"
    if p.returncode == 0:
        return True, ""
    lines = [ln for ln in ((p.stderr or "") + (p.stdout or "")).splitlines() if ln.strip()]
    last = lines[-1].strip() if lines else ""
    try:
        err = json.loads(last)["error"]
        return False, f"{err.get('code')}: {err.get('message')}"
    except Exception:
        return False, last or f"herdr agent prompt exited {p.returncode}"


class CardTime:
    """An event supplied by its producer, never a Deck observation time."""

    def __init__(self, label: str, value: object = None, source: str = ""):
        self.label = label
        self.source = source or "Event timestamp not exported by Firstmate"
        self.producer_kind = ""
        self.event_id = ""
        self.rejected_producer = ""
        self.exported_value = ""
        self.producer_context: dict[str, str] = {}
        self.rejected_context: list[str] = []
        self.epoch: float | None = None
        self.day: date | None = None
        self.problem = "not recorded"
        if value is None or value == "":
            return
        try:
            if isinstance(value, bool):
                raise ValueError("boolean is not a timestamp")
            if isinstance(value, (int, float)):
                # Status [at=] values are Unix seconds, not milliseconds.
                stamp = datetime.fromtimestamp(value, timezone.utc)
                if value <= 0:
                    raise ValueError("nonpositive epoch")
            elif isinstance(value, str):
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    self.day = date.fromisoformat(value)
                    self.problem = ""
                    return
                stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    self.problem = "timezone missing; exact instant unavailable"
                    return
                if not re.search(r"[T ]\d{2}:\d{2}:\d{2}", value):
                    self.problem = "seconds not recorded; exact instant unavailable"
                    return
            else:
                raise ValueError("unsupported timestamp")
            epoch = stamp.timestamp()
            # Offsets near the datetime range limits may normalize outside
            # representable UTC/local dates even when fromisoformat succeeds.
            datetime.fromtimestamp(epoch, timezone.utc).astimezone()
            self.epoch = epoch
            self.problem = ""
        except (ValueError, TypeError, OverflowError, OSError):
            self.problem = "invalid timestamp"


_CARD_TIME_KINDS = {
    "charted": {"filed": "Filed", "queued": "Queued", "asked": "Asked"},
    "captains_call": {"asked": "Asked"},
    "underway": {"updated": "Updated"},
    "awaiting_merge": {"ready": "Ready", "done_report": "Done report"},
    "landed": {"delivered": "Delivered"},
}
_CARD_TIME_COMPACT = {
    "Filed": ("Fld", "F"), "Queued": ("Que", "Q"), "Asked": ("Ask", "A"),
    "Updated": ("Upd", "U"), "Ready": ("Rdy", "R"),
    # Column context distinguishes a done report from a landed delivery.
    "Done report": ("Rpt", "D"), "Delivered": ("Del", "D"),
}


def producer_card_time(raw: object, bucket: str, fallback: CardTime) -> CardTime:
    """Consume the optional bearings event contract without guessing semantics.

    Null/missing exports retain the existing subset. Malformed exports retain
    that same fallback and expose the rejection in details, not as its source.
    """
    if raw is None:
        return fallback

    def reject(reason: str) -> CardTime:
        fallback.rejected_producer = reason
        return fallback

    def text_field(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip()) and all(
            unicodedata.category(ch) not in ("Cc", "Cs") for ch in value
        )

    if not isinstance(raw, dict):
        return reject("expected an object")
    kind = raw.get("kind")
    allowed = _CARD_TIME_KINDS.get(bucket, {})
    if not isinstance(kind, str) or kind not in allowed:
        return reject("event kind does not match this column")
    precision = raw.get("precision")
    if precision not in ("date", "second"):
        return reject("precision must be date or second")
    source = raw.get("source")
    if not text_field(source):
        return reject("source must be nonempty plain text")
    event_id = raw.get("event_id")
    if event_id is not None and not text_field(event_id):
        return reject("event_id must be nonempty plain text when supplied")
    value = raw.get("value")
    if not isinstance(value, str) or not value:
        return reject("value must be an ISO date or offset timestamp string")
    event = CardTime(allowed[kind], value, source)
    if event.problem:
        return reject(event.problem)
    if (precision == "date") != (event.day is not None):
        return reject("value does not match declared precision")
    event.producer_kind = kind
    event.exported_value = value
    # Identity is display-only and is never synthesized from polling or time.
    event.event_id = event_id or ""
    for name in ("original_value", "attempt_boundary", "attempt", "current_attempt"):
        context = raw.get(name)
        if context is None:
            continue
        if name == "original_value" and isinstance(context, int) and not isinstance(context, bool):
            context = str(context)
        if text_field(context):
            event.producer_context[name] = context
        else:
            event.rejected_context.append(name)
    return event


def card_time_line(event: CardTime, width: int, now: float | None = None) -> str:
    """Recompute only the age on repaint; the event remains immutable."""
    now = time.time() if now is None else now
    calendar_day = event.day
    if event.day is not None:
        age = event.day.isoformat() + " (date only)"
    elif event.epoch is None:
        age = "unknown" if event.problem == "not recorded" else "unavailable"
    else:
        seconds = now - event.epoch
        if seconds < 0:
            age = "future · check clock"
        elif seconds < 60:
            age = "just now"
        elif seconds >= 365 * 86400:
            calendar_day = datetime.fromtimestamp(event.epoch, timezone.utc).astimezone().date()
            age = calendar_day.isoformat()
        else:
            for step, unit, short in ((604800, "week", "w"), (86400, "day", "d"),
                                      (3600, "hour", "h"), (60, "minute", "m")):
                if seconds >= step:
                    count = int(seconds // step)
                    age = f"{count} {unit}{'s' if count != 1 else ''} ago"
                    if display_width(f"{event.label} {age}") > width:
                        age = f"{count}{short} ago"
                    break
    line = f"{event.label} {age}"
    if display_width(line) <= width:
        return line
    short_label, tag = _CARD_TIME_COMPACT.get(event.label, (event.label, event.label[:1]))
    if calendar_day is not None:
        month = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")[calendar_day.month - 1]
        short_date = f"{calendar_day.day:02d}{month}"
        candidates = (f"{event.label} {calendar_day.isoformat()}", f"{short_label} {calendar_day.isoformat()}",
                      f"{short_label} {short_date}", f"{tag} {short_date}", f"{tag}{short_date}", short_date)
    elif event.epoch is None:
        marker = "?" if event.problem == "not recorded" else "!"
        candidates = (f"{short_label} {marker}", f"{tag}{marker}", marker)
    elif age.endswith(" ago"):
        compact_age = age[:-4]
        candidates = (f"{short_label} {compact_age}", f"{tag} {compact_age}", compact_age)
    elif age == "just now":
        candidates = (f"{short_label} now", f"{tag} now", "now")
    else:  # future clock warning
        candidates = (f"{event.label} future", f"{short_label} future", f"{tag} fut", "future")
    return next((candidate for candidate in candidates if display_width(candidate) <= width), clip(candidates[-1], width))


def card_time_detail(event: CardTime, *, translate=None) -> str:
    tr = translate or _presentation_text
    if event.day is not None:
        value = tr('{date} (date only; time and timezone not recorded)', date=event.day.isoformat())
    elif event.epoch is not None:
        utc = datetime.fromtimestamp(event.epoch, timezone.utc)
        value = tr('{local} (terminal local); {utc} (UTC)', local=utc.astimezone().isoformat(), utc=utc.isoformat())
        if event.epoch > time.time():
            value += tr('; future timestamp: check producer/terminal clock')
    else:
        value = tr(event.problem) if translate else event.problem
    detail = tr('{label}: {value}. Source: {source}.', label=tr(event.label), value=value, source=event.source)
    if event.producer_kind:
        precision = "date" if event.day is not None else "second"
        detail += tr(' Producer event: {kind}; precision: {precision}.', kind=event.producer_kind, precision=precision)
    if event.event_id:
        detail += tr(' Event ID: {id}.', id=event.event_id)
    if event.exported_value:
        detail += tr(' Exported value: {value}.', value=event.exported_value)
    context_labels = {"original_value": "Original value", "attempt_boundary": "Attempt boundary",
                      "attempt": "Attempt", "current_attempt": "Current attempt (context only)"}
    for name, context in event.producer_context.items():
        detail += tr(' {label}: {value}.', label=tr(context_labels[name]), value=context)
    if event.rejected_context:
        detail += tr(' Ignored invalid producer context: {fields}.', fields=", ".join(event.rejected_context))
    if event.rejected_producer:
        detail += tr(' Producer card_time ignored: {reason}; fallback shown.', reason=event.rejected_producer)
    return detail


def task_update_time(path: str, spawn_epoch: float = 0) -> tuple[str, int]:
    """Latest retained authored declaration, excluding liveness/control text.

    Missing stamps on a newer declaration invalidate earlier precision. A
    bounded tail or current-spawn boundary may leave no trustworthy event.
    """
    for line in reversed(_status_lines(path)):
        match = re.match(r"^([a-zA-Z_-]+)\s*((?:\[[^\]]*\]\s*)*):\s*(.*)$", line)
        if not match:
            continue
        state, envelope, text = match.groups()
        if state not in ("working", "paused", "parked", "blocked", "failed", "done", "needs-decision", "resolved"):
            continue
        # Support the same legacy metadata position as blocker parsing.
        lead = re.match(r"^((?:\[(?:at|key|resolve-key)=[^\]]*\]\s*)+)", text)
        if lead:
            envelope += lead.group(0)
            text = text[lead.end():]
        if re.search(r"\b(?:heartbeat|poll(?:ing)?|liveness)\b", text, re.IGNORECASE):
            continue
        if not task_report_text(text) and state not in ("blocked", "needs-decision"):
            continue
        at = re.search(r"\[at=(\d+)\]", envelope)
        epoch = _int(at.group(1)) if at else 0
        if spawn_epoch and epoch and epoch < spawn_epoch:
            return "", 0
        return state, epoch
    return "", 0


class Card:
    __slots__ = (
        "id",
        "task",
        "bucket",
        "title",
        "badge",
        "badge_color",
        "doing",
        "agent",
        "account",
        "model",
        "effort",
        "kind",
        "mode",
        "worktree",
        "branch",
        "pane_id",
        "workspace_id",
        "tab_id",
        "artifact",
        "blocked_by",
        "live_status",
        "snapshot_state",
        "status_state",
        "activity_unconfirmed",
        "status_text",
        "report_text",
        "declaration",
        "report_at",
        "blockers",
        "owner_label",
        "owner",
        "home_path",
        "delivery_identity",
        "delivery_summary",
        "delivery_problem",
        "event_time",
        "run_elapsed",
        "run_tokens",
        "spawn_epoch",
        # Set on every Captain's Call row: this question is answered and its
        # owner has not taken the work yet.
        "answered_hidden",
        "flow",
        "dependencies",
        "wait_reason",
        "supervisor",
        # Structured liveness and wait inputs for card_activity(). hold_bucket
        # and hold_until (gate, in-flight and secondmate rows), wait_until and
        # state_source (in-flight rows) come from a companion Firstmate change
        # and stay absent until it lands. Absent producer fields stay
        # empty/None; they are never read from prose.
        "run_step",
        "endpoint_exists",
        "state_source",
        "hold_bucket",
        "hold_until",
        "wait_until",
        # Receipt time of a snapshot kept after its refresh failed; 0 if fresh.
        "retained_at",
    )


def _trunc(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "\u2026"


def make_cards(
    home: Home,
    snap: dict,
    meta_index: dict[str, dict[str, str]],
    agents: dict[str, dict],
    panes: dict[str, dict],
    homes: list[Home] | None = None,
    reveal_answered: bool = False,
    retained_at: float = 0.0,
    bearings: dict[str, tuple[dict, float]] | None = None,
) -> tuple[dict[str, list[Card]], dict[str, int]]:
    """Bucket snapshot entries into the workflow columns.

    Returns ``(columns, totals)`` where totals counts the entries per column
    before the Landed cap, so the UI can show e.g. ``Landed (10/18)``.

    A call the captain already answered stays in the column and carries
    ``answered_hidden``; each view decides whether to show it, and ``totals``
    counts only the actionable calls.
    """
    cols: dict[str, list[Card]] = {key: [] for key, _ in COLUMNS}
    totals: dict[str, int] = {key: 0 for key, _ in COLUMNS}
    home_list = list(homes) if homes else [home]
    known_bearings = (bearings if bearings is not None
                      else {delivery_home_identity(home): (snap, time.time())})
    account_names = load_account_names(home_list)

    paths: dict[str, dict] = {}
    for p in snap.get("paths") or []:
        if isinstance(p, dict) and p.get("id"):
            paths[p["id"]] = p
            paths.setdefault(_strip_owner(p["id"]), p)
    # Exact row ids only: a secondmate child must not borrow a main task's probe.
    endpoints: dict[str, bool] = {
        e["id"]: e["exists"] for e in snap.get("endpoints") or []
        if isinstance(e, dict) and isinstance(e.get("id"), str) and isinstance(e.get("exists"), bool)
    }

    def populate(card: Card, entry: dict) -> None:
        card.run_elapsed = ""
        card.run_tokens = ""
        card.spawn_epoch = 0.0
        raw_id = entry.get("id") or entry.get("key") or "?"
        card.id = raw_id
        card.task = _strip_owner(raw_id)
        owner_path = home.path
        time_home = home
        if "/" in raw_id:
            owner = resolve_owner_home(home_list, _owner_prefix(raw_id), None)
            time_home = owner
            if owner is not None and owner.path:
                owner_path = owner.path
        if card.bucket == 'landed':
            owner, _problem = delivery_owner_home(home_list, home, entry.get('owner') or '', raw_id)
            time_home = owner
            owner_path = owner.path if owner is not None and not is_remote_home(owner) else ''
        card.kind = (entry.get("kind") or "").strip()
        card.blocked_by = (entry.get("blocked_by") or "").strip()
        card.artifact = (entry.get("artifact") or "").strip()
        card.owner = ""
        card.home_path = home.path
        card.delivery_identity = delivery_home_identity(home)
        card.delivery_summary = ""
        card.delivery_problem = ""
        card.flow = entry.get("flow") if isinstance(entry.get("flow"), dict) else {}
        if isinstance(card.flow.get('title'), str) and card.flow['title'].strip():
            card.title = card.flow['title']
        supervisor = entry.get('supervisor', snap.get('supervisor') if owner_path == home.path else None)
        card.supervisor = supervisor if isinstance(supervisor, dict) else {}
        card.dependencies = []
        card.wait_reason = ""
        card.owner_label = next((h.label for h in home_list if h.path == owner_path), home.label)
        card.run_step = entry.get("run_step") if isinstance(entry.get("run_step"), dict) else {}
        card.endpoint_exists = endpoints.get(raw_id)
        card.retained_at = retained_at
        card.state_source, card.hold_bucket, card.hold_until, card.wait_until = (
            entry.get(key) if isinstance(entry.get(key), str) else ""
            for key in ("state_source", "hold_bucket", "hold_until", "wait_until"))

        p = paths.get(raw_id) or paths.get(card.task) or {}
        card.worktree = p.get("worktree") or ""

        meta_path = (meta_index.get(owner_path) or {}).get(card.task, "")
        meta = read_meta(meta_path) if meta_path else {}
        card.agent = meta.get("harness", "")
        card.account = account_label(meta, account_names.get(owner_path) or {})
        card.model = meta.get("model", "")
        card.effort = meta.get("effort", "")
        card.mode = meta.get("mode", "")
        card.branch = meta.get("branch", "")
        card.spawn_epoch = parse_spawn_gen_epoch(meta.get("spawn_gen", ""))
        if not card.worktree:
            card.worktree = meta.get("worktree", "")
        card.pane_id = meta.get("herdr_pane_id", "")
        card.workspace_id = meta.get("herdr_workspace_id", "")
        card.tab_id = meta.get("herdr_tab_id", "")

        # stale meta: verify against live panes
        if card.pane_id and card.pane_id not in panes:
            fallback = ""
            for pid, pane in panes.items():
                cwds = {os.path.realpath(pane.get("cwd") or ""), os.path.realpath(pane.get("foreground_cwd") or "")}
                if card.worktree and os.path.realpath(card.worktree) in cwds:
                    fallback = pid
                    break
            card.pane_id = fallback or card.pane_id

        live = agents.get(card.pane_id) if card.pane_id in panes else None
        card.live_status = (live or {}).get("agent_status", "")
        if live and not card.agent:
            card.agent = live.get("agent", "")

        status_file = (p.get("status") or os.path.join(owner_path, "state", f"{card.task}.status")) if card.bucket != 'landed' else ''
        st_state, st_text, st_at = read_status_tail(status_file) if status_file else ("", "", 0)
        card.status_state = st_state
        card.status_text = st_text
        _, card.report_text, card.report_at = read_task_report(status_file) if status_file else ("", "", 0)
        card.declaration = read_task_report(status_file, card.spawn_epoch, durable=True) if status_file else ("", "", 0)
        card.blockers = read_open_blockers(status_file, card.spawn_epoch, card.kind) if status_file else []
        # Preserve the complete latest wait declaration, including useful prose
        # that the conservative progress/receipt filter intentionally rejects.
        for line in reversed(_status_lines(status_file) if status_file else []):
            match = re.match(r"^(paused|parked)((?:\s+\[[^\]]*\])*)\s*:\s*(.*)$", line)
            if match:
                at = re.search(r"\[at=(\d+)\]", match[2])
                if not card.spawn_epoch or not at or int(at[1]) >= card.spawn_epoch:
                    card.wait_reason = match[3]
                break
            if re.match(r"^(working|done|failed|resolved|blocked|needs-decision)\b", line):
                break
        if card.bucket == "charted":
            # Filing is known; entry into the queue is not necessarily known.
            card.event_time = CardTime("Filed", entry.get("filed"), "bearings.gates.filed (task filing date)")
        elif card.bucket == "captains_call":
            # A regenerated board/decision card is not a new question. Nor is
            # the original task's filing date the start of its current hold.
            card.event_time = CardTime("Asked", source="Current question/hold creation is not exported; task filing and board generation are not question time")
        elif card.bucket == "landed":
            card.event_time = CardTime("Delivered", entry.get("closed"), "bearings.landed.closed; if absent, open the delivery for its retained completion date")
        else:
            update_state, update_at = ("", 0)
            if status_file and time_home is not None and not is_remote_home(time_home):
                update_state, update_at = task_update_time(status_file, card.spawn_epoch)
            completed = entry.get("state") == "done" and not (entry.get("doing") or "").lower().startswith("harness state unavailable")
            label = "Done report" if completed else "Updated"
            if completed and update_state != "done":
                update_at = 0
            boundary = "current-spawn boundary applied" if card.spawn_epoch else "spawn boundary unavailable"
            card.event_time = CardTime(label, update_at or None,
                f"{status_file}: latest retained meaningful task declaration [at=Unix seconds]; {boundary}; "
                + ("worker done report, not delivery or review-request time" if completed
                   else "excludes poll/heartbeat, harness activity and control receipts"))
            if time_home is None or is_remote_home(time_home):
                card.event_time.source = "No verified local owner/task declaration export; remote or unresolved owner timestamps are not inferred"
        time_bucket = 'awaiting_merge' if card.bucket == 'underway' and entry.get('state') == 'done' and not (entry.get('doing') or '').lower().startswith('harness state unavailable') else card.bucket
        card.event_time = producer_card_time(entry.get("card_time"), time_bucket, card.event_time)

        # ---- badge ----
        doing = (entry.get("doing") or "").lower()
        state = (entry.get("state") or "").lower()
        card.snapshot_state = state
        # Herdr's native activity is a separate observation, not confirmation
        # of Firstmate's runtime verdict. Keep the original inputs for --once.
        card.activity_unconfirmed = (
            state not in ("working", "blocked", "parked", "paused", "failed", "done")
            or doing.startswith("harness state unavailable")
        )
        if card.bucket == "charted":
            if card.blocked_by and card.blocked_by != "-":
                card.badge, card.badge_color = "\u23f3 dependency", C_WARN
            else:
                card.badge, card.badge_color = "\u00b7 queued", C_QUEUE
        elif card.bucket == "captains_call":
            card.badge, card.badge_color = "\u2691 pending", C_DECIDE
        elif card.bucket == "landed":
            card.badge, card.badge_color = "\u2713 landed", C_OK
        else:
            # Explicit task outcomes must survive a busy pane (which may be
            # doing follow-up work). When confirmation is unavailable, the log
            # can still report a pause or outcome, but cannot prove activity or
            # readiness to merge. Do not move a reported done row to Awaiting.
            task_state = state
            reported = card.activity_unconfirmed and state not in (
                "blocked", "parked", "paused", "failed", "done"
            )
            if reported:
                task_state = st_state
                # A declaration from a prior spawn is historical evidence,
                # not the current worker's workflow phase.
                if card.spawn_epoch and st_at and st_at < card.spawn_epoch:
                    task_state = ""
            task_badges = {
                "blocked": ("\u26d4 blocked", C_BAD),
                "parked": ("\u23f8 parked", C_QUEUE),
                "paused": ("\u23f8 paused", C_QUEUE),
                "failed": ("\u26d4 failed", C_BAD),
                "needs-decision": ("\u2691 decision", C_DECIDE),
                "done": ("\u2713 done", C_DIM),
            }
            # The native source may describe a pane's activity truthfully
            # without establishing Firstmate's semantic task verdict.
            native_badges = {
                "working": ("\u25cf Herdr working", C_OK),
                "blocked": ("\u26d4 Herdr blocked", C_BAD),
                "idle": ("\u25cb Herdr idle", C_DIM),
            }
            kind = card_activity(card)["kind"]
            if card.blockers:
                blocked = any(b["state"] == "blocked" for b in card.blockers)
                card.badge, card.badge_color = task_badges["blocked" if blocked else "needs-decision"]
                card.badge += " \u00b7 reported"
            elif card.bucket == "awaiting_merge":
                # Firstmate says the crew is done; the card belongs to Awaiting
                # Merge even if the pane is busy again (e.g. merge follow-up).
                card.badge, card.badge_color = "\u25cd awaits merge", C_REVIEW
            elif task_state in task_badges:
                card.badge, card.badge_color = task_badges[task_state]
                if card.activity_unconfirmed:
                    card.badge += " \u00b7 reported"
            elif card.activity_unconfirmed:
                card.badge, card.badge_color = native_badges.get(
                    card.live_status, ("? unknown", C_WARN)
                )
            elif card.live_status == "blocked":
                card.badge, card.badge_color = "\u26d4 Herdr blocked", C_BAD
            elif st_state == "needs-decision":
                card.badge, card.badge_color = "\u2691 decision", C_DECIDE
            elif "validating" in doing or kind == "validating":
                card.badge, card.badge_color = "\u25d0 validating", C_WARN
            elif kind == "working":
                card.badge, card.badge_color = "\u25cf shipping", C_OK
            else:
                card.badge, card.badge_color = native_badges.get(
                    card.live_status, ("? unknown", C_WARN)
                )

    for entry in snap.get("gates") or []:
        c = Card()
        c.bucket = "charted"
        c.title = entry.get("title") or ""
        reason = (entry.get("reason") or "").strip()
        c.doing = "" if reason in ("-", "") else reason
        populate(c, entry)
        cols["charted"].append(c)
        totals["charted"] += 1

    for entry in snap.get("in_flight") or []:
        c = Card()
        state = (entry.get("state") or "").lower()
        # Every in-flight row remains Underway, including integration work.
        c.bucket = "underway"
        c.title = entry.get("name") or ""
        c.doing = (entry.get("doing") or "").strip()
        populate(c, entry)
        cols[c.bucket].append(c)
        totals[c.bucket] += 1

    for entry in snap.get("decisions_open") or []:
        c = Card()
        c.bucket = "captains_call"
        # the durable title and hold reason arrive beside the collapsed summary,
        # so a ticket with no composed card still has real context
        c.title = entry.get("title") or entry.get("summary") or ""
        reason = (entry.get("reason") or "").strip()
        c.doing = reason or (entry.get("verb") or "").strip()
        populate(c, entry)
        # The row's owner names the home whose backlog holds the call, so the
        # answer runs the intake in that home rather than the board on screen.
        c.owner = (entry.get("owner") or "").strip()
        c.home_path = ""
        c.home_path = answer_home_path(c, homes, home)
        # Like a delivery, the call belongs to its answering home; a parent's
        # mirror carries the owner's identity so the fleet merge folds it.
        answering = resolve_owner_home(homes, c.owner, home) if c.home_path else None
        c.delivery_identity = delivery_home_identity(answering) if answering else ""
        cols["captains_call"].append(c)
        totals["captains_call"] += 1

    # An answered call leaves the column until its owning agent takes the work.
    # Firstmate keeps a live captain hold after an answer, so without this the
    # same row returns with no new question and the calls pile up.
    if hide_answered_default() and not reveal_answered:
        records = load_answered_calls()
        if records:
            now = time.time()
            present = {answered_call_key(card.home_path, card.task) for card in cols["captains_call"] if card.home_path}
            hidden = 0
            for card in cols["captains_call"]:
                content = decision_card_content(card, homes, home, bearings=known_bearings)
                digest = call_digest(
                    content.get("key") or card.task,
                    content.get("title") or "",
                    content.get("options"),
                    content.get("close") or "",
                )
                record = records.get(answered_call_key(card.home_path, card.task)) if card.home_path else None
                if record is None and card.task in records and card.home_path:
                    if _legacy_answer_unambiguous(card.task, home_list, card.home_path, known_bearings):
                        record = records[card.task]
                if isinstance(record, dict) and not _same_path(record.get("home") or "", card.home_path):
                    record = None
                card.answered_hidden = answered_row_hidden(
                    record, content.get("stamp") or "", digest, now
                )
                if card.answered_hidden:
                    hidden += 1
            totals["captains_call"] = max(0, totals["captains_call"] - hidden)
            totals["captains_call_hidden"] = hidden
            # A record whose row left the column, or an old one, is spent.
            spent = [
                key
                for key, record in records.items()
                if isinstance(record, dict)
                and (
                    (_same_path(record.get("home") or "", home.path)
                     and answered_call_key(record["home"], _answered_call_task(key)) not in present)
                    or now - _float(record.get("answered_at"), 0.0) > ANSWERED_TTL_SECS
                )
            ]
            if spent:
                for key in spent:
                    records.pop(key, None)
                save_answered_calls(records)

    landed_entries = snap.get("landed") or []
    totals["landed"] = len(landed_entries)
    for entry in landed_entries[:LANDED_LIMIT] if LANDED_LIMIT else landed_entries:
        c = Card()
        c.bucket = "landed"
        c.title = entry.get("what") or ""
        c.doing = ""
        populate(c, entry)
        c.owner = (entry.get("owner") or "").strip()
        owner_home, c.delivery_problem = delivery_owner_home(home_list, home, c.owner, c.id)
        c.home_path = c.delivery_identity = ""
        # Unresolved owners remain snapshot-only, even when a local task has
        # the same slug. All is a view, never an artifact root.
        if owner_home is not None:
            c.home_path = owner_home.path
            c.owner_label = owner_home.label
            c.delivery_identity = delivery_home_identity(owner_home)
            c.delivery_summary = delivery_card_summary(owner_home, c.task)
        cols["landed"].append(c)

    # Dependency IDs alone are not actionable. Prefer the producer's explicit
    # relation, then resolve exact IDs within the same owning home only.
    local_rows = {(c.home_path, c.task): c for cards in cols.values() for c in cards}
    canonical = {}
    if any(c.blocked_by not in ('', '-') for c in local_rows.values()) and not is_remote_home(home):
        try:
            cfg = _tasks_read({'config': delivery_read(home.path, '.tasks.toml', 65536)})
            if cfg.get('backend', 'markdown') == 'markdown':
                path = (cfg.get('markdown') or {}).get('path') or 'data/backlog.md'
                canonical = {r['id']: r for r in _tasks_read({'text': delivery_read(home.path, path, DELIVERY_INDEX_LIMIT)})}
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            pass
    for c in local_rows.values():
        explicit = c.flow.get('dependencies')
        if isinstance(explicit, list):
            c.dependencies = [d for d in explicit if isinstance(d, dict) and isinstance(d.get('id'), str)]
        if not c.dependencies:
            for key in (c.blocked_by.split(',') if c.blocked_by not in ('', '-') else []):
                key = key.strip()
                scope_known = '/' not in c.id or resolve_owner_home(home_list, _owner_prefix(c.id), None) is not None
                match = local_rows.get((c.home_path, key)) if c.home_path and scope_known else None
                row = canonical.get(key, {}) if c.home_path == home.path and scope_known else {}
                c.dependencies.append({'id': key, 'title': match.title if match else row.get('title', ''),
                                       'owner': c.owner_label if match or row else '',
                                       'state': match.snapshot_state if match else row.get('state', '')})
    return cols, totals



# Delivery reads use tasks-axi's installed grammar, never a second Markdown task
# parser. No backend methods (including pruning) run. Only bounded bytes already
# opened beneath this home are sent to Node; artifact paths never become code.
_TASKS_READER = r"""
import {pathToFileURL} from 'node:url';
let input = '';
for await (const chunk of process.stdin) input += chunk;
const data = JSON.parse(input);
const root = process.argv[1];
if (data.config !== undefined) {
  const {parseConfigToml} = await import(pathToFileURL(root + '/config.js'));
  console.log(JSON.stringify(parseConfigToml(data.config)));
} else {
  const {parseBacklog} = await import(pathToFileURL(root + '/backends/markdown-grammar.js'));
  // tasks-axi archives use dated batch headings, not live section names.
  // Adapt only those headings in memory, then use its unchanged row grammar.
  const text = data.archive ? data.text.replace(/^## Archived \d{4}-\d{2}-\d{2}\r?$/gm, '## Done') : data.text;
  const rows = parseBacklog(text).sections.flatMap(s => s.entries)
    .filter(e => e.task).map(e => ({...e.task, raw: e.raw.join('\n')}));
  console.log(JSON.stringify(rows));
}
"""
DELIVERY_FILE_LIMIT = 512 * 1024
DELIVERY_INDEX_LIMIT = 16 * 1024 * 1024
DELIVERY_TOTAL_LIMIT = 2 * 1024 * 1024


def delivery_text(value) -> str:
    """Untrusted prose stays terminal text, including Markdown/HTML and URLs."""
    return ''.join(c for c in str(value) if c in '\n\t' or not unicodedata.category(c).startswith('C'))


def delivery_read(home: str, path: str, limit: int) -> str:
    """Read a regular file beneath home without traversing any symlink (also
    protects against a rename/symlink race). Absolute paths must be inside it.
    Oversize files are refused visibly rather than silently cutting a report.
    """
    if not safe_file_reads():
        raise OSError('Safe report reads require descriptor-relative no-follow support; use a POSIX backend (WSL on Windows).')
    if '..' in path.split('/'):
        raise ValueError('unsafe traversal path')
    root = os.path.realpath(home)
    if os.path.isabs(path):
        # Resolve the directory chain like root (macOS /var -> /private/var) but
        # never the leaf, which the O_NOFOLLOW open must see as-is.
        path = os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
    relative = os.path.relpath(path, root) if os.path.isabs(path) else path
    parts = relative.split('/')
    if not home or any(p in ('', '.', '..') for p in parts) or '\\' in path or '\x00' in path:
        raise ValueError('unsafe path outside allowed home/task')
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(leaf)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('not a regular text file')
            if info.st_size > limit:
                raise ValueError(f'file exceeds {limit} byte read limit')
            with os.fdopen(leaf, 'rb', closefd=False) as stream:
                data = stream.read(limit + 1)
            if len(data) > limit:
                raise ValueError(f'file exceeds {limit} byte read limit')
            return data.decode('utf-8', errors='replace')
        finally:
            os.close(leaf)
    finally:
        os.close(fd)


def _tasks_read(data):
    env = _child_env()
    cli = shutil.which('tasks-axi', path=env['PATH'])
    node = shutil.which('node', path=env['PATH'])
    if not cli or not node:
        raise ValueError('History needs installed tasks-axi (Node) canonical Markdown reader')
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(cli))), 'src')
    result = subprocess.run([node, '--input-type=module', '-e', _TASKS_READER, root],
                            input=json.dumps(data), text=True, capture_output=True, timeout=15,
                            env=env)
    if result.returncode:
        raise ValueError('tasks-axi canonical reader unavailable/incompatible; need parseConfigToml and parseBacklog exports')
    return json.loads(result.stdout)


def delivery_records(home: Home, task_id: str = "", *, translate=None) -> tuple[list[dict], str]:
    """Current and configured archive, without changing done_keep or any rows.
    Unsupported backends/remotes report the missing surface explicitly.
    """
    tr = translate or _presentation_text
    if is_remote_home(home) or is_aggregate_home(home):
        return [], tr('History unavailable here; choose a local home. Remote history needs a read-only archive export from Firstmate.')
    # New public, read-only contract; old homes retain the canonical-reader
    # fallback below. Failure is visible instead of silently hiding archives.
    endpoint = endpoint_path(home.path, 'fm-delivery-history.sh')
    endpoint_warning = ''
    if os.path.isfile(endpoint):
        try:
            env = _child_env()
            env['FM_HOME'] = home.path
            result = subprocess.run(shell_command(endpoint, '--json'), env=env, capture_output=True,
                                    text=True, timeout=15, stdin=subprocess.DEVNULL)
            data = json.loads(result.stdout)
            if result.returncode or data.get('schema') != 'fm-delivery-history.v1' or os.path.realpath(data.get('home', '')) != os.path.realpath(home.path):
                raise ValueError(tr('invalid history response or different home', 'respuesta de historial inválida o de otro home'))
            rows = []
            for record in data.get('records', []):
                if not isinstance(record, dict) or not isinstance(record.get('id'), str) or not KEY_RE.fullmatch(record['id']):
                    raise ValueError(tr('invalid history record', 'registro de historial inválido'))
                row = dict(record, home_path=home.path, owner_label=home.label,
                           delivery_identity=delivery_home_identity(home))
                completion = record.get('completion')
                if isinstance(completion, dict):
                    row.setdefault('closed', completion.get('value') or completion.get('date') or completion.get('closed') or '')
                    row.setdefault('verb', completion.get('verb') or '')
                else:
                    row.setdefault('closed', completion if isinstance(completion, str) else '')
                row.setdefault('title', record['id'])
                row.setdefault('source', 'Firstmate history')
                if not task_id or record['id'] == task_id:
                    rows.append(row)
            note = tr('Retained Firstmate history. ', 'Historial retenido de Firstmate. ') + json.dumps(data.get('retention', {}), ensure_ascii=False)
            note += tr(' · Sources: ', ' · Fuentes: ') + json.dumps(data.get('sources', []), ensure_ascii=False)
            note += tr(' · Recent is limited; history includes retained records only and cannot recover deleted records.', ' · Recientes es una vista limitada; historial sólo incluye lo conservado, no recupera registros borrados.')
            if not data.get('complete'):
                note += tr(' · incomplete: ') + '; '.join(map(str, data.get('limitations') or []))
            if not task_id or rows:
                return rows, note
            # History intentionally excludes reopened tasks. Preserve the
            # existing current-record check when opening a stale Landed card.
            endpoint_warning = tr('Public history does not retain this delivery; checking the current canonical record for reopening.', 'El historial público no retiene esta entrega; se consulta el registro canónico actual para detectar reapertura.')
        except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
            endpoint_warning = tr('History export unavailable: {error}; local canonical reader fallback', 'History export unavailable: {error}; respaldo del lector canónico local', error=exc)
    notes, rows, active_ids = ([endpoint_warning] if endpoint_warning else []), {}, set()
    try:
        config = _tasks_read({'config': delivery_read(home.path, '.tasks.toml', 65536)})
    except FileNotFoundError:
        config = {}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return [], f'History unavailable: {exc}'
    if config.get('backend', 'markdown') != 'markdown':
        return [], 'History unavailable: this backend needs a read-only completed-record/archive export from Firstmate/tasks-axi.'
    cfg = config.get('markdown') or {}
    backlog = cfg.get('path') or 'data/backlog.md'
    archive = cfg.get('archive') or os.path.join(os.path.dirname(backlog), 'done-archive.md')
    for source, path in [('current', backlog), ('archive', archive)]:
        try:
            text = delivery_read(home.path, path, DELIVERY_INDEX_LIMIT)
            parsed = _tasks_read({'text': text, 'archive': source == 'archive'})
            for task in parsed:
                key = task['id']
                if source == 'current':
                    active_ids.add(key)
                elif key in active_ids:
                    continue  # current/reopened state wins over an old archive
                if task_id and key != task_id:
                    continue
                inspect_current = bool(task_id and source == 'current')
                if task.get('state') != 'done' and not inspect_current:
                    continue
                # Same delivery boundary as fm-landed-lib.sh: answered captain
                # questions and reportless scouts are not shipped work.
                kind = task.get('kind', '')
                hold = task.get('hold') or {}
                if not inspect_current and (kind == 'captain' or hold.get('kind') == 'captain'):
                    continue
                links = task.get('links') or []
                reports = [x['url'] for x in links if x.get('kind') == 'report']
                prs = [x['url'] for x in links if x.get('kind') == 'pr']
                raw = task.get('raw', '').split('\n', 1)[0]
                title = task.get('title', '')
                tail = raw[raw.find(title) + len(title):] if title in raw else ''
                if not inspect_current and re.search(r'\(hold-kind:\s*captain\)', tail):
                    continue  # provenance can outlive the hold reason in tasks-axi
                completion = re.search(r'\((merged|reported|done|closed) (\d{4}-\d{2}-\d{2})\)', tail)
                verb = completion[1] if completion else ''
                local = title.endswith('local main')
                if not inspect_current and kind == 'scout' and not reports:
                    continue
                if not inspect_current and kind != 'scout' and (reports or prs or local) and not (
                    (verb == 'merged' and prs) or (verb == 'done' and local)
                ):
                    continue
                task.update(source=source, home_path=home.path, owner_label=home.label,
                            delivery_identity=delivery_home_identity(home),
                            verb=verb, artifact=(reports[0] if kind == 'scout' and reports else
                                                prs[0] if verb == 'merged' and prs else
                                                'local main' if local else ''))
                if key not in rows or (source == "archive" and key not in active_ids and
                                       task.get("closed", "") >= rows[key].get("closed", "")):
                    rows[key] = task
            notes.append(f'{source}: {path}')
        except FileNotFoundError:
            notes.append(tr('{source}: {path} missing; retained history may be incomplete', source=source, path=path))
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            notes.append(tr('{source} unavailable: {error}; retained history may be incomplete', source=source, error=exc))
    ordered = sorted(rows.values(), key=lambda r: (r.get('closed', ''), r['id']), reverse=True)
    return ordered, '; '.join(notes) + tr('. Retained deliveries only; no retention changes. done_keep={count}: current backlog limit; archive retains existing records. Recent is not all history; deleted records cannot be recovered.', '. Retained deliveries only; no retention changes. done_keep={count}: límite del backlog actual; el archivo conserva los registros que aún existan. Recientes no es todo el historial; no se recuperan registros borrados.', count=cfg.get('done_keep', tr('not specified', 'no especificado')))


def delivery_home_identity(home: Home) -> str:
    # Two remote hosts can have the same filesystem path. Include the route
    # and its owning local home; never treat that path as a local artifact root.
    if is_aggregate_home(home) or not home.path:
        return ''
    if is_remote_home(home):
        return 'remote:' + json.dumps([os.path.realpath(home.via), home.route, home.path])
    return os.path.realpath(home.path)


def delivery_owner_home(homes: list[Home], source: Home, owner: str, task: str) -> tuple[Home | None, str]:
    """Resolve only producer ownership and discovered identities, never a slug,
    cwd, basename, or first matching ambiguous alias. Decision routing is separate.
    """
    claims = list(dict.fromkeys(x for x in (owner if owner != '(main)' else '', _owner_prefix(task)) if x))
    resolved = None
    for explicit in claims:
        matches = {delivery_home_identity(h): h for h in real_homes(homes)
                   if explicit in (h.mate_id, h.label, h.label.removeprefix('2ndmate-'))}
        matches.pop('', None)
        if len(matches) == 1:
            match = next(iter(matches.values()))
            if resolved is not None and delivery_home_identity(resolved) != delivery_home_identity(match):
                return None, 'Delivery owner unavailable: producer owner and task namespace conflict; repair the owning home export.'
            resolved = match
            continue
        problem = 'ambiguous' if matches else 'unknown'
        return None, f'Delivery owner unavailable ({problem}: {explicit}); choose the owning home or repair its discovered identity.'
    if resolved is not None:
        return resolved, ''
    if delivery_home_identity(source):
        return source, ''
    return None, 'Delivery owner unavailable: All has no filesystem home; reopen this delivery from its owning crew tab.'


def delivery_task_prefix(task: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', task) or task in ('.', '..'):
        raise ValueError('Unsafe task path refused; no artifact read.')
    return f'data/{task}/'


def delivery_excerpt(text: str) -> str:
    """Quote authored prose; headings/links are presentation, never outcomes.
    Prefer an explicitly authored Result section; no semantic completion guess.
    """
    result = re.search(r'^#{1,6}\s+(?:Result|Resultado)\s*:?\s*\n(.*?)(?=^#{1,6}\s|\Z)', text, re.M | re.S | re.I)
    text = result[1] if result else text
    for paragraph in re.split(r'\n\s*\n', text):
        prose = '\n'.join(line for line in paragraph.splitlines()
                          if not re.match(r'^\s*(?:#|\||```|~~~)', line))
        prose = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', prose)
        prose = re.sub(r'[*`_]', '', prose)
        prose = ' '.join(prose.split())
        if re.match(r'^(ready in branch|(?:para|for) (?:root|firstmate)\b|owner application status|head\s*:|commit\s*:)', prose, re.I):
            continue  # A shipping receipt is not an explanation of the result.
        if prose:
            return delivery_text(prose)
    return ''


def delivery_card_summary(home: Home, task: str) -> str:
    if is_remote_home(home) or not delivery_home_identity(home):
        return ''
    try:
        prefix = delivery_task_prefix(task)
        for name in ('delivery.md', 'recap.md', 'report.md'):
            try:
                text = delivery_read(home.path, prefix + name, DELIVERY_FILE_LIMIT)
                excerpt = delivery_excerpt(text)
                if excerpt:
                    return excerpt
            except FileNotFoundError:
                continue
    except (OSError, ValueError):
        pass  # Full reader explains unavailable/unsafe artifacts explicitly.
    return ''


def delivery_key(home: str, task: str) -> str:
    identity = home if home.startswith('remote:') else os.path.realpath(home)
    return hashlib.sha256((identity + '\0' + task).encode()).hexdigest()


def delivery_seen(home: str, task: str) -> bool:
    try:
        records = json.loads(delivery_read(_plugin_state_dir(), 'deliveries-read.json', DELIVERY_INDEX_LIMIT))
        return bool(records.get(delivery_key(home, task)))
    except (OSError, ValueError, AttributeError):
        return False


def delivery_mark_read(home: str, task: str) -> str:
    """Only explicit delivery opens call this. This is no acceptance/approval."""
    directory = _plugin_state_dir()
    path = os.path.join(directory, 'deliveries-read.json')
    try:
        os.makedirs(directory, exist_ok=True)
        # Lock concurrent board instances so opening one home loses no marks
        # written by a different instance. The ledger is never a task authority.
        import tempfile
        with file_lock(path + '.lock'):
            try:
                records = json.loads(delivery_read(directory, 'deliveries-read.json', DELIVERY_INDEX_LIMIT))
                if not isinstance(records, dict):
                    records = {}
            except (OSError, ValueError):
                records = {}
            records[delivery_key(home, task)] = int(time.time())
            fd, tmp = tempfile.mkstemp(prefix='deliveries-', dir=directory)
            try:
                with os.fdopen(fd, 'w') as stream:
                    json.dump(records, stream)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        return ''
    except OSError:
        return 'Read mark could not be saved; this delivery may still appear new.'


def readable_markdown(text: str) -> str:
    """Terminal presentation only; retained source remains a separate view."""
    text = delivery_text(text)
    text = re.sub(r'\[([^\]\n]+)\]\([^\n]*?\)', r'\1', text)
    text = re.sub(r'^\s{0,3}#{1,6}\s+', '', text, flags=re.M)
    text = re.sub(r'^\s*```[^\n]*$', '', text, flags=re.M)
    text = re.sub(r'\*\*([^*\n]+)\*\*|__([^_\n]+)__', lambda m: m[1] or m[2], text)
    text = re.sub(r'`([^`\n]+)`', r'\1', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def concise_delivery(text: str, title: str, *, translate=None) -> str:
    """Quote authored content, with explicit bounds and no generated claims."""
    tr = translate or _presentation_text
    plain = readable_markdown(re.sub(r'\A\s*# [^\n]*\n+', '', text))
    if not plain:
        return readable_markdown(title) + '\n\n' + tr('No summary is retained. Read the report or ask Firstmate for an explanation.', 'No hay un resumen retenido. Consulta el informe o pide una explicación a Firstmate.')
    explicit_result = re.search(r'^#{1,6}\s+(?:Result|Resultado)\s*:?', text, re.M | re.I)
    technical_receipt = re.match(r'^(ready in branch|(?:para|for) (?:root|firstmate)\b|owner application status|head\s*:|commit\s*:)', plain, re.I)
    if not explicit_result and (len(plain) > 1500 or technical_receipt):
        return readable_markdown(title) + '\n\n' + tr('The delivery retains a technical report but no brief result summary. Read Details or ask Firstmate for an explanation.', 'La entrega conserva un informe técnico, pero no un resumen breve del resultado. Consulta Detalles o pide una explicación a Firstmate.')
    if len(plain) <= 1500:
        return plain
    # A technical recap can be hundreds of lines. Preserve it in Details, and
    # quote its result paragraph rather than dumping it into the first view.
    excerpt = delivery_excerpt(text)
    excerpt = readable_markdown(excerpt)
    if len(excerpt) > 900:
        excerpt = excerpt[:900].rsplit(' ', 1)[0] + '…'
    return excerpt + '\n\n' + tr('Abbreviated summary of retained content. Details retains the full text.', 'Resumen abreviado del contenido retenido. Detalles conserva el texto completo.')


def local_time_caption(event: CardTime, now: float | None = None) -> str:
    now = time.time() if now is None else now
    if event.day is not None:
        return event.day.isoformat() + ' · sólo fecha; hora no registrada'
    if event.epoch is None:
        return 'Fecha y hora no registradas' if event.problem == 'not recorded' else 'Fecha no disponible; consulta Detalles'
    stamp = datetime.fromtimestamp(event.epoch, timezone.utc).astimezone()
    seconds = now - event.epoch
    if seconds < 0:
        age = 'fecha futura; revisar reloj'
    elif seconds < 60:
        age = 'ahora'
    else:
        age = next(f'hace {int(seconds // step)} {unit}' for step, unit in
                   ((604800, 'sem'), (86400, 'd'), (3600, 'h'), (60, 'min')) if seconds >= step)
    return stamp.strftime('%Y-%m-%d %H:%M %Z') + ' · ' + age


def compact_time_caption(event: CardTime, width: int) -> str:
    full = local_time_caption(event)
    if display_width(full) <= width:
        return full
    if event.epoch is not None:
        stamp = datetime.fromtimestamp(event.epoch, timezone.utc).astimezone()
        age = full.split(' · ')[-1].replace('hace ', '')
        compact = stamp.strftime('%d/%m %H:%M') + ' · ' + age
        return compact if display_width(compact) <= width else clip(age, width)
    if event.day is not None:
        return event.day.isoformat() if width >= 10 else event.day.strftime('%d/%m')
    return 'Sin fecha/hora' if width >= 14 else '?'


def delivery_content(record: dict, home: Home, board_time: CardTime | None = None, *, split_views: bool = False, translate=None, report_heading=True):
    tr = translate or _presentation_text
    task = record['id']
    if not delivery_home_identity(home):
        return tr('Delivery owner unavailable: choose the owning crew tab; All is not a filesystem home.')
    header = tr('Task: {task}\nTitle: {title}\nHome: {home}\nKind: {kind}\nState: {state}\nCompletion: {verb} · {closed}\nSource: {source}\nScope: this delivery/task only; broader intent is not inferred.\nRead marks are local reading history, not acceptance or approval.\n', task=task,
                title=record.get('title') or record.get('what') or task,
                home=home.label, kind=record.get('kind') or tr('not recorded'),
                state=record.get('state') or 'landed (snapshot)',
                verb=record.get('verb') or tr('not recorded'),
                closed=record.get('closed') or tr('date unavailable'),
                source=record.get('source') or tr('recent snapshot'))
    header += tr('Completion time: ') + card_time_detail(CardTime(
        "Delivered", record.get("closed"),
        f"{record.get('source') or 'recent snapshot'} canonical completion field closed"), translate=translate) + "\n"
    if board_time is not None:
        header += tr('Board time: ') + card_time_detail(board_time, translate=translate) + "\n"
    if record.get('body'):
        header += tr('\nCanonical task notes:\n') + record['body'] + '\n'
    if record.get('artifact') not in ('', '-', None):
        header += tr('\nRecorded artifact: ') + record['artifact'] + '\n'
    if is_remote_home(home):
        return delivery_text(tr('Remote report/recap unavailable: no local artifact export. Ask the owning home for a read-only report export. Snapshot text may be abbreviated.\n\n') + header)
    try:
        prefix = delivery_task_prefix(task)
    except ValueError:
        return delivery_text(header + tr('\nUnsafe task path refused; no artifact read.'))
    queue = [prefix + 'delivery.md', prefix + 'recap.md', prefix + 'report.md']
    for link in record.get('links') or []:
        if link.get('kind') == 'report':
            queue.append(link['url'])
    if record.get('artifact', '').endswith(('.md', '.json', '.txt')):
        queue.append(record['artifact'])
    # Known receipt convention and locally linked receipts, no directory crawl.
    queue += [prefix + 'application-receipt.json', prefix + 'package-receipt.json']
    seen, documents, sections, total = set(), [], [], 0
    while queue and len(seen) < 32:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        relative = os.path.relpath(path, os.path.realpath(home.path)) if os.path.isabs(path) else path
        if '..' in path.split('/') or not relative.startswith(prefix) or not relative.endswith(('.md', '.txt', '.json')):
            sections.append(tr('Artifact refused (outside task/text scope): {path}', path=path))
            continue
        try:
            text = delivery_read(home.path, relative, DELIVERY_FILE_LIMIT)
            total += len(text.encode())
            if total > DELIVERY_TOTAL_LIMIT:
                sections.append(tr('Artifact total exceeds 2 MiB read limit; remaining artifacts unavailable.'))
                break
            documents.append((relative, text))
            # Render linked local text inline. Never execute HTML, shell, URL
            # handlers, or follow links outside this exact task directory.
            for target in re.findall(r'\[[^\]\n]*\]\(([^)\n]+)\)', text):
                target = target.strip().strip('<>')
                if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', target):
                    sections.append(tr('External link (text only, not opened): {target}', target=target))
                elif target.endswith(('.md', '.json', '.txt')):
                    queue.append(target if os.path.isabs(target) or target.startswith('data/') else
                                 os.path.join(os.path.dirname(relative), target))
        except FileNotFoundError:
            if path not in (prefix + 'delivery.md', prefix + 'recap.md', prefix + 'report.md',
                            prefix + 'application-receipt.json', prefix + 'package-receipt.json'):
                sections.append(tr('Artifact missing: {path}', path=path))
        except (OSError, ValueError) as exc:
            sections.append(tr('Artifact unavailable: {path}: {error}', path=path, error=exc))
    if queue:
        sections.append(tr('Artifact link/read limit reached (32 paths); remaining artifacts not read.'))
    curated = next(((p, t) for p, t in documents if p in (prefix + 'delivery.md', prefix + 'recap.md')
                    and delivery_excerpt(t)), None)
    report = next(((p, t) for p, t in documents if p == prefix + 'report.md' and t.strip()), None)
    if report is None:
        report = next(((p, t) for p, t in documents if p not in (prefix + 'delivery.md', prefix + 'recap.md')
                       and p.endswith(('.md', '.txt')) and t.strip()), None)
    lead = tr('Full report: r / [Report]\n\n') if report else tr('Report unavailable: no full report retained for this task.\n\n')
    if curated:
        lead += re.sub(r'\A\s*# [^\n]*\n+', '', curated[1]).strip() + '\n\n'
    else:
        lead += tr('No curated recap (delivery.md or recap.md); showing retained report prose where available.\n\n')
    ordered = []
    if report:
        ordered.append(tr('Full report: {path}\n{text}', path=report[0], text=report[1]))
    for path, text in documents:
        if curated and path == curated[0] or report and path == report[0]:
            continue
        label = tr('Curated recap (home-authored)') if path in (prefix + 'delivery.md', prefix + 'recap.md') else tr('Artifact')
        ordered.append(tr('{label}: {path}\n{text}', label=label, path=path, text=text))
    details = tr('Delivery details\n') + header
    if curated:
        details += tr('\nCurated recap (home-authored): ') + curated[0]
    if record.get('state') and record['state'] != 'done':
        lead = tr('Current task state: {state}; the retained delivery below is historical.\n\n', state=record['state']) + lead
    full = delivery_text(lead + '\n\n'.join(ordered) + '\n\n' + details + '\n\n' + '\n\n'.join(sections))
    if split_views:
        summary = concise_delivery(curated[1] if curated else '', record.get('title') or record.get('what') or task, translate=translate)
        if record.get('state') and record['state'] != 'done':
            summary = tr('The task was reopened; this delivery is historical.', 'La tarea se reabrió; esta entrega es histórica.') + '\n\n' + summary
        return {'summary': summary,
                'report': readable_markdown(tr('Full report · {path}\n\n{text}', 'Informe completo · {path}\n\n{text}', path=report[0], text=report[1]) if report_heading else report[1]) if report else tr('No full report is retained for this task.', 'No hay informe completo retenido para esta tarea.'),
                'details': readable_markdown(details + '\n\n' + (curated[1] if curated else '') + '\n\n' + '\n\n'.join(ordered[1:] if report else ordered) + '\n\n' + '\n\n'.join(sections))}
    return full


# ----------------------------------------------------------------------------
# other agents: Herdr agents that are not Firstmate panes
# ----------------------------------------------------------------------------

# Shown on the All tab alongside optional Recent Landed.
OTHER_AGENTS_COLUMN = ("agents", "Other Agents")
# Firstmate's own label for a task workspace (fm_backend_herdr_projection_
# workspace_label in its bin/backends/herdr.sh). It covers a crew spawned since
# the last meta index rebuild, or one from a home the deck does not discover.
_FM_CHILD_WORKSPACE_RE = re.compile(r"^└ .+ · p:[A-Za-z0-9_-]{22}$")
_AGENT_ORDER = {"blocked": 0, "done": 1, "working": 2, "idle": 3}
_AGENT_BADGES = {
    "blocked": ("\u26d4 blocked", C_BAD),
    "done": ("\u2713 done", C_REVIEW),
    "working": ("\u25cf working", C_OK),
    "idle": ("\u25cb idle", C_DIM),
}


def firstmate_pane_ids(meta_index: dict[str, dict[str, str]]) -> set[str]:
    """Every Herdr pane a Firstmate task record names (state/<task>.meta)."""
    out: set[str] = set()
    for tasks in meta_index.values():
        for meta_path in tasks.values():
            pane = read_meta(meta_path).get("herdr_pane_id", "")
            if pane:
                out.add(pane)
    return out


def track_agent_states(
    since: dict[str, tuple[str, float, bool]], agents: dict[str, dict], now: float
) -> dict[str, tuple[str, float, bool]]:
    """pane_id -> (status, since_epoch, change_seen).

    Herdr does not timestamp a state change, so the deck times it itself. A
    state already present when the deck first saw the pane is at least that
    old (change_seen False); a change seen on a tick starts its own clock.
    """
    out: dict[str, tuple[str, float, bool]] = {}
    for pid, agent in agents.items():
        status = agent.get("agent_status") or "unknown"
        prev = since.get(pid)
        if prev is None:
            out[pid] = (status, now, False)
        elif prev[0] == status:
            out[pid] = prev
        else:
            out[pid] = (status, now, True)
    return out


def other_agent_cards(
    state: dict[str, dict[str, dict]],
    homes: list[Home],
    fm_panes: set[str],
    since: dict[str, tuple[str, float, bool]],
    now: float,
) -> list[Card]:
    """One card per Herdr agent that is not a Firstmate pane, blocked first.

    A Firstmate pane is one a task meta names, one whose cwd is a Firstmate
    home (the supervisor), or one in a Firstmate task workspace.
    """
    home_paths = {os.path.realpath(h.path) for h in homes if h.path}
    workspaces = state.get("workspaces") or {}
    tabs = state.get("tabs") or {}
    cards: list[Card] = []
    for pid, agent in (state.get("agents") or {}).items():
        ws_id = agent.get("workspace_id") or ""
        ws_label = (workspaces.get(ws_id) or {}).get("label") or ""
        cwds = {os.path.realpath(agent[k]) for k in ("cwd", "foreground_cwd") if agent.get(k)}
        if pid in fm_panes or cwds & home_paths or _FM_CHILD_WORKSPACE_RE.match(ws_label):
            continue
        status = agent.get("agent_status") or "unknown"
        tab_id = agent.get("tab_id") or ""
        tab_label = (tabs.get(tab_id) or {}).get("label") or ""
        tab = f"tab {tab_label}" if tab_label.isdigit() else tab_label
        c = Card()
        c.id = ws_label or ws_id or pid
        c.task = pid
        c.bucket = OTHER_AGENTS_COLUMN[0]
        c.badge, c.badge_color = _AGENT_BADGES.get(status, (f"? {status}", C_DIM))
        c.title = agent.get("terminal_title_stripped") or agent.get("title") or ""
        held = since.get(pid)
        if held and held[0] == status:
            age = _format_elapsed(now - held[1])
            c.doing = f"for {age}" if held[2] else f"for \u2265{age}"
        else:
            c.doing = ""
        c.agent = agent.get("display_agent") or agent.get("agent") or ""
        c.account = ""
        c.model = c.effort = c.kind = c.worktree = c.branch = ""
        c.mode = f"{tab} \u00b7 {pid}" if tab else pid
        c.pane_id, c.workspace_id, c.tab_id = pid, ws_id, tab_id
        c.artifact = c.blocked_by = c.status_text = c.owner = c.home_path = ""
        c.live_status = status
        c.run_elapsed = c.run_tokens = ""
        c.spawn_epoch = 0.0
        cards.append(c)
    cards.sort(key=lambda c: (_AGENT_ORDER.get(c.live_status, 4), c.id.lower(), c.pane_id))
    return cards


# ----------------------------------------------------------------------------
# collector thread
# ----------------------------------------------------------------------------


class Snapshot:
    def __init__(self) -> None:
        self.seq = 0
        self.home: Home | None = None
        self.cols: dict[str, list[Card]] = {k: [] for k, _ in COLUMNS}
        self.collected_at = 0.0
        self.homes: list[Home] = []
        self.bearings: dict[str, tuple[dict, float]] = {}
        self.counts: dict[str, int] = {}
        self.landed_counts: dict[str, int] = {}
        self.totals: dict[str, int] = {}
        # Calls the captain answered that are still off the board.
        self.hidden_calls = 0
        self.activity: dict[str, str] = {}
        self.loading = False
        self.error = ""
        self.agents: dict[str, dict] = {}
        self.panes: dict[str, dict] = {}
        self.others: list[Card] = []
        # label -> epoch of the last good remote snapshot, only while a
        # remote fetch is failing; the board shows "offline, last seen".
        self.offline: dict[str, float] = {}


class Collector(threading.Thread):
    """Two-tier collector.

    Every tick (``FM_FLOW_TICK_SECS``) it rebuilds the cards of the active home
    from cheap sources: Herdr's live agent/pane list plus each task's
    ``state/<id>.meta`` and the tail of ``state/<id>.status`` (both cached by
    mtime). The expensive bearings snapshot only runs when it is older than
    ``FM_FLOW_BEARINGS_SECS``, when the crew switches to an uncached home, or on
    an explicit refresh. Only the active home is rebuilt, so other crews do not
    cost anything while you look at one board.
    """

    def __init__(self, active_label: str = "") -> None:
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self._snap = Snapshot()
        # Nothing is collected yet: the first paint says so instead of showing
        # empty columns, which read as a home with no work.
        self._snap.loading = True
        self._active = active_label
        self._reveal_answered = False
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._force = False
        self._last_discover = 0.0
        self._raw: dict[str, tuple[dict, float, str]] = {}
        self._meta_index: dict[str, dict[str, str]] = {}
        self._meta_at = 0.0
        self._counts: dict[str, int] = {}
        self._landed_counts: dict[str, int] = {}
        self._fleet_dupes = 0
        self._since: dict[str, tuple[str, float, bool]] = {}
        self._others: list[Card] = []
        self._remote_offline: dict[str, float] = {}

    # -- public API ---------------------------------------------------------
    def snapshot(self) -> Snapshot:
        with self.lock:
            return self._snap

    def set_active(self, label: str) -> None:
        self._active = label
        self._wake.set()

    def refresh_now(self) -> None:
        self._force = True
        self._wake.set()

    def set_reveal_answered(self, value: bool) -> None:
        """Show the calls the captain already answered. The build leaves them
        off the board, so the flag lives here and a forced refresh republishes
        them."""
        self._reveal_answered = bool(value)
        self.refresh_now()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    # -- internals ----------------------------------------------------------
    def _discover(self, force: bool = False) -> list[Home]:
        now = time.time()
        if force or now - self._last_discover > HOMES_REDISCOVER_SECS:
            self._last_discover = now
            homes = discover_homes()
            if homes:
                return homes
        with self.lock:
            return self._snap.homes or discover_homes()

    def _publish(self, snap: Snapshot) -> None:
        with self.lock:
            snap.seq = self._snap.seq + 1
            self._snap = snap

    def _refresh_agents(
        self, homes: list[Home], now: float
    ) -> tuple[dict[str, dict], dict[str, dict]]:
        """Read Herdr once for this tick and rebuild the other-agents cards."""
        state = herdr_state()
        agents = state["agents"]
        self._since = track_agent_states(self._since, agents, now)
        self._others = other_agent_cards(
            state, real_homes(homes), firstmate_pane_ids(self._meta_index), self._since, now
        )
        return agents, state["panes"]

    @staticmethod
    def _activity(homes: list[Home], agents: dict[str, dict]) -> dict[str, str]:
        live: dict[str, str] = {}
        for a in agents.values():
            cwd = os.path.realpath(a.get("cwd") or "")
            if not cwd:
                continue
            status = a.get("agent_status") or ""
            if live.get(cwd) != "working" or status == "blocked":
                live[cwd] = status
        out = {
            h.label: live.get(os.path.realpath(h.path), "")
            for h in homes
            if not is_aggregate_home(h)
        }
        if any(is_aggregate_home(h) for h in homes):
            statuses = list(out.values())
            agg = ""
            if any(s == "working" for s in statuses):
                agg = "working"
            elif any(s == "blocked" for s in statuses):
                agg = "blocked"
            elif any(s for s in statuses):
                agg = next(s for s in statuses if s)
            out[ALL_CREW_LABEL] = agg
        return out

    def _bearings_stale(self, home: Home, now: float) -> bool:
        raw, ts, raw_path = self._raw.get(home.label, ({}, 0.0, ""))
        return (
            self._force
            or not raw
            or raw_path != home.path
            or now - ts > _bearings_ttl()
        )

    def _cached_bearings(self, homes: list[Home]) -> dict[str, tuple[dict, float]]:
        return {
            delivery_home_identity(home): (raw, received_at)
            for home in real_homes(homes)
            for raw, received_at, path in [self._raw.get(home.label, ({}, 0.0, ""))]
            if raw and path == home.path
        }

    def _load_bearings(
        self,
        home: Home,
        now: float,
        agents: dict[str, dict],
        panes: dict[str, dict],
        homes: list[Home],
    ) -> tuple[dict, dict[str, list[Card]], dict[str, int], bool, str]:
        """Return raw snapshot, cards, totals, loading flag, and error text."""
        raw, ts, raw_path = self._raw.get(home.label, ({}, 0.0, ""))
        stale = self._bearings_stale(home, now)
        loading = False
        retained_at = 0.0
        if stale:
            if not raw:
                loading = True
            data = bearings_snapshot(home)
            if data:
                raw, ts, raw_path = data, time.time(), home.path
                self._raw[home.label] = (raw, ts, raw_path)
                loading = False
                self._remote_offline.pop(home.label, None)
            else:
                retained_at = ts
                if home.route:
                    # Keep the last good snapshot; the board shows its age
                    # instead of blanking the tab while the host is unreachable.
                    self._remote_offline[home.label] = time.time()
        if not raw:
            # A remote home with no cached snapshot yet is offline, not an
            # error: the tab carries the offline marker itself.
            err = "" if home.route else f"bearings snapshot failed for {home.path}"
            return {}, {k: [] for k, _ in COLUMNS}, {}, loading, err
        cols, totals = make_cards(
            home, raw, self._meta_index, agents, panes, homes, reveal_answered=self._reveal_answered,
            retained_at=retained_at, bearings=self._cached_bearings(homes),
        )
        enrich_cards_run_stats(cols, agents)
        return raw, cols, totals, loading, ""

    def _record_home_counts(self, home: Home, cols: dict[str, list[Card]], totals: dict[str, int]) -> None:
        total, landed = board_ticket_count(cols, totals)
        self._counts[home.label] = total
        self._landed_counts[home.label] = landed

    def _publish_fleet(
        self,
        homes: list[Home],
        agents: dict[str, dict],
        panes: dict[str, dict],
        now: float,
    ) -> None:
        aggregate = next(h for h in homes if is_aggregate_home(h))
        parts: list[tuple[Home, dict[str, list[Card]], dict[str, int]]] = []
        loading = False
        errors: list[str] = []
        for home in real_homes(homes):
            raw, cols, totals, home_loading, err = self._load_bearings(
                home, now, agents, panes, homes
            )
            loading = loading or home_loading
            if err:
                errors.append(err)
            if raw:
                parts.append((home, cols, totals))
                self._record_home_counts(home, cols, totals)
        cols, totals = merge_fleet_columns(parts) if parts else ({k: [] for k, _ in COLUMNS}, {})
        merged_active = sum(totals.get(bucket, 0) for bucket in ACTIVE_BUCKETS)
        fleet_total = fleet_planned_running_count(homes, self._counts, self._landed_counts)
        if fleet_total is not None:
            if parts:
                self._fleet_dupes = max(0, fleet_total - merged_active)
            self._counts[ALL_CREW_LABEL] = max(0, fleet_total - self._fleet_dupes)
        error = "; ".join(errors)
        self._publish(
            self._snapshot(
                homes,
                aggregate,
                cols,
                agents,
                panes,
                totals,
                loading=loading and not parts,
                error=error,
            )
        )
        self._force = False

    def _snapshot(
        self,
        homes: list[Home],
        active: Home,
        cols: dict[str, list[Card]],
        agents: dict[str, dict],
        panes: dict[str, dict],
        totals: dict[str, int] | None = None,
        loading: bool = False,
        error: str = "",
    ) -> Snapshot:
        snap = Snapshot()
        snap.homes = homes
        snap.bearings = self._cached_bearings(homes)
        snap.home = active
        snap.cols = cols
        snap.hidden_calls = int((totals or {}).get("captains_call_hidden", 0))
        snap.others = list(self._others)
        snap.offline = {
            label: self._raw.get(label, ({}, 0.0, ""))[1]
            for label in self._remote_offline
        }
        if is_aggregate_home(active):
            snap.cols = {**cols, OTHER_AGENTS_COLUMN[0]: list(self._others)}
        snap.agents = agents
        snap.panes = panes
        snap.totals = totals or {}
        snap.activity = self._activity(homes, agents)
        snap.counts = dict(self._counts)
        snap.landed_counts = dict(self._landed_counts)
        snap.collected_at = time.time()
        snap.loading = loading
        snap.error = error
        return snap

    def run(self) -> None:
        _debug("collector start")
        _debug(f"deck herdr-firstmate-flow rev={_deck_revision() or 'unknown'}")
        while not self._stop.is_set():
            started = time.time()
            try:
                _debug("iter: discover")
                homes = self._discover()
                _debug(f"iter: homes={len(homes)}")
                for home in homes:
                    if not is_aggregate_home(home):
                        _debug(
                            f"iter: home {home.label} "
                            f"firstmate={_git_revision(home.path) or 'unknown'} "
                            f"path={home.path}"
                        )
                if not homes:
                    snap = Snapshot()
                    snap.error = "no Firstmate homes found (set FM_FLOW_HOMES or homes.conf)"
                    self._publish(snap)
                else:
                    active = next((h for h in homes if h.label == self._active), homes[0])
                    self._active = active.label
                    _debug(
                        f"iter: agents for {active.label} "
                        f"firstmate={_git_revision(active.path) or 'unknown'}"
                    )
                    now = time.time()
                    if now - self._meta_at > 60:
                        self._meta_index = build_meta_index(homes)
                        self._meta_at = now
                    _debug(f"iter: meta={len(self._meta_index)}")
                    agents, panes = self._refresh_agents(homes, now)
                    _debug(
                        f"iter: agents={len(agents)} panes={len(panes)} "
                        f"others={len(self._others)}"
                    )

                    if is_aggregate_home(active):
                        _debug("iter: fleet board")
                        self._publish_fleet(homes, agents, panes, now)
                    else:
                        raw, ts, raw_path = self._raw.get(active.label, ({}, 0.0, ""))
                        stale = self._bearings_stale(active, now)
                        if stale:
                            if raw:
                                cached, cached_totals = make_cards(
                                    active,
                                    raw,
                                    self._meta_index,
                                    agents,
                                    panes,
                                    homes,
                                    reveal_answered=self._reveal_answered,
                                    bearings=self._cached_bearings(homes),
                                )
                                enrich_cards_run_stats(cached, agents)
                            else:
                                cached, cached_totals = {k: [] for k, _ in COLUMNS}, {}
                            _debug("iter: publish cached")
                            self._publish(
                                self._snapshot(
                                    homes,
                                    active,
                                    cached,
                                    agents,
                                    panes,
                                    cached_totals,
                                    loading=not raw,
                                )
                            )
                            _debug("iter: bearings begin")
                            data = bearings_snapshot(active)
                            _debug(f"iter: bearings done ok={bool(data)}")
                            if data:
                                raw, ts, raw_path = data, time.time(), active.path
                                self._raw[active.label] = (raw, ts, raw_path)

                        _debug("iter: make_cards")
                        if raw:
                            cols, totals = make_cards(
                                active,
                                raw,
                                self._meta_index,
                                agents,
                                panes,
                                homes,
                                reveal_answered=self._reveal_answered,
                                bearings=self._cached_bearings(homes),
                            )
                            enrich_cards_run_stats(cols, agents)
                        else:
                            cols, totals = {k: [] for k, _ in COLUMNS}, {}
                        self._record_home_counts(active, cols, totals)
                        fleet_total = fleet_planned_running_count(
                            homes,
                            self._counts,
                            self._landed_counts,
                            self._fleet_dupes,
                        )
                        if fleet_total is not None:
                            self._counts[ALL_CREW_LABEL] = fleet_total
                        error = "" if raw else f"bearings snapshot failed for {active.path}"
                        _debug("iter: publish final")
                        self._publish(
                            self._snapshot(homes, active, cols, agents, panes, totals, error=error)
                        )
                        self._force = False
            except Exception as exc:  # never kill the UI thread
                _debug(f"iter: error {exc!r}")
                snap = Snapshot()
                snap.error = f"collector error: {exc}"
                self._publish(snap)
            elapsed = time.time() - started
            _debug(f"iter: end {elapsed:.2f}s")
            self._wake.wait(timeout=max(0.25, TICK_SECS - elapsed))
            self._wake.clear()


# ----------------------------------------------------------------------------
# rendering
# ----------------------------------------------------------------------------


def display_width(text: str) -> int:
    """Terminal cells used by text.

    ``len()`` is not enough: glyphs like U+26D4 NO ENTRY (the blocked badge)
    render as two-cell emoji, which would push a card's right border out of
    alignment. East Asian Wide/Fullwidth count as 2, combining marks as 0.
    """
    cells = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        cells += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return cells


def clip(text: str, width: int) -> str:
    text = text.replace("\n", " ")
    if display_width(text) <= width:
        return text
    out: list[str] = []
    used = 0
    for ch in text:
        w = display_width(ch)
        if used + w > max(0, width - 1):
            break
        out.append(ch)
        used += w
    return "".join(out) + "\u2026"


def pad(text: str, width: int) -> str:
    text = clip(text, width)
    return text + " " * max(0, width - display_width(text))


def _wrap_long_token(token: str, width: int) -> list[str]:
    """Split a single word across lines when it exceeds width."""
    if display_width(token) <= width:
        return [token]
    parts: list[str] = []
    cur = ""
    for ch in token:
        trial = cur + ch
        if cur and display_width(trial) > width:
            parts.append(cur)
            cur = ch
        else:
            cur = trial
    if cur:
        parts.append(cur)
    return parts or [""]


def wrap_text(text: str, width: int) -> list[str]:
    """Greedy word wrap on terminal cells; never returns an empty list."""
    width = max(4, width)
    lines: list[str] = []
    for para in str(text).splitlines() or [""]:
        words = para.split()
        if not words:
            lines.append("")
            continue
        cur = ""
        for word in words:
            for piece in _wrap_long_token(word, width):
                if not cur:
                    cur = piece
                elif display_width(cur) + 1 + display_width(piece) <= width:
                    cur += " " + piece
                else:
                    lines.append(cur)
                    cur = piece
        if cur:
            lines.append(cur)
    return lines or [""]


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def ansi_width(text: str) -> int:
    return display_width(_ANSI_RE.sub("", text))


def supervisor_caption(value: dict) -> str:
    if not value:
        return 'Firstmate: actividad actual desconocida (sin declaración).'
    event = CardTime('Declared', value.get('observed_at'), value.get('source', ''))
    ttl = _float(value.get('ttl', value.get('ttl_seconds')), 0)
    stale = value.get('freshness') == 'stale' or bool(event.epoch and ttl > 0 and time.time() - event.epoch > ttl)
    age = local_time_caption(event)
    if stale:
        return f"Firstmate: declaración vencida; actividad actual desconocida. {value.get('summary') or ''} · {age}"
    declared = {'coordinating': 'coordinación', 'waiting': 'espera', 'idle': 'inactividad'}.get(value.get('state'), 'estado desconocido')
    return f"Firstmate declaró {declared}: {value.get('summary') or 'sin propósito registrado'} · {age}. Actividad del modelo desconocida."


def durable_report(card: Card) -> tuple[str, str, CardTime]:
    """Authored task evidence has no native-activity TTL or merge authority."""
    flow = getattr(card, 'flow', {}) or {}
    local_state, local_text, local_at = getattr(card, 'declaration', ('', '', 0))
    declared = flow.get('declared_state')
    states = ('working', 'paused', 'parked', 'blocked', 'failed', 'done', 'needs-decision')
    if declared in states and flow.get('reason'):
        event = getattr(card, 'event_time', None)
        # Only a declaration timestamp belongs to this report. Filing, ready,
        # delivery and collection times must never supply missing precision.
        if not (isinstance(event, CardTime) and event.source == 'status.declaration.at'
                and event.producer_kind in ('updated', 'done_report')):
            same = declared == local_state and flow['reason'] == local_text
            event = CardTime('Updated', (local_at or None) if same else None,
                             flow.get('source') or 'flow.declared_state')
        return declared, flow['reason'], event
    return local_state, local_text, CardTime('Updated', local_at or None, 'task status declaration [at]')


def flow_context(card: Card, *, translate=None) -> dict[str, str]:
    """Workflow semantics come from declarations, never a prose classifier."""
    tr = translate or _presentation_text
    flow = getattr(card, 'flow', {}) or {}
    blockers = getattr(card, 'blockers', [])
    report_state, report_text, _report_time = durable_report(card)
    phase = flow.get('phase')
    state = getattr(card, 'snapshot_state', '')
    confirmed_work = state == 'working' and not getattr(card, 'activity_unconfirmed', False)
    reason = flow.get('reason') or (blockers[0]['text'] if blockers else '') or ('' if confirmed_work else getattr(card, 'wait_reason', ''))
    current = task_report_text(card.doing)
    reason = reason or ((report_text or current) if getattr(card, 'activity_unconfirmed', False) else (current or report_text))
    if phase == 'active' and flow and flow.get('state') != 'executing':
        phase = 'unknown'
    if phase not in ('human', 'dependency', 'integration', 'technical', 'active'):
        if card.bucket == 'captains_call':
            phase = 'human'
        elif card.blocked_by not in ('', '-'):
            phase = 'dependency'
        elif blockers or state in ('paused', 'parked', 'failed', 'blocked'):
            phase = 'technical'
        elif state == 'done' and not getattr(card, 'activity_unconfirmed', False):
            phase = 'integration'
        elif state == 'working' and not getattr(card, 'activity_unconfirmed', False) and (not flow or flow.get('state') == 'executing'):
            phase = 'active'
        elif report_state in ('paused', 'parked') or getattr(card, 'wait_reason', ''):
            phase = 'waiting'
        elif report_state in ('blocked', 'failed', 'needs-decision'):
            phase = 'technical'
        elif report_state in ('done', 'working'):
            phase = 'reported'
        else:
            phase = 'unknown'
    labels = {'human': tr('Waiting for a human answer', 'Espera respuesta humana'), 'dependency': tr('Waiting for a dependency', 'Espera dependencia'),
              'integration': tr('Integration pending', 'Integración pendiente'), 'technical': tr('Technical hold', 'Retención técnica'),
              'active': tr('Declared activity', 'Actividad declarada'), 'unknown': tr('Unconfirmed state', 'Estado sin confirmar'),
              'waiting': tr('Declared wait', 'Espera declarada'),
              'reported': tr('Work reported', 'Trabajo informado') if report_state == 'done' else tr('Last report', 'Último reporte')}
    if phase == 'integration' and flow.get('state') == 'ready':
        labels['integration'] = tr('Ready for integration', 'Lista para integración')
    actions = {'human': tr('Answer the question in Captain’s Call.', 'Responder la pregunta en Captain’s Call.'),
               'dependency': tr('Firstmate reviews the dependency and resumes when it is released.', 'Firstmate revisa la dependencia y retoma al liberarse.'),
               'integration': tr('Firstmate verifies and integrates pending work.', 'Firstmate verifica e integra el trabajo pendiente.'),
               'technical': tr('Firstmate reviews the reason and condition for resuming.', 'Firstmate revisa el motivo y la condición para retomar.'),
               'active': tr('Firstmate supervises the work and its results.', 'Firstmate supervisa el trabajo y sus resultados.'),
               'unknown': tr('Firstmate must confirm the task’s state.', 'Firstmate debe confirmar el estado de la tarea.'),
               'waiting': tr('Firstmate reviews the declared condition and coordinates the next step.', 'Firstmate revisa la condición declarada y coordina el siguiente paso.'),
               'reported': tr('Firstmate reviews the result and outstanding work.', 'Firstmate revisa el resultado y los pendientes del encargo.')}
    supervisor = getattr(card, 'supervisor', {}) or {}
    supervision = supervisor_caption(supervisor)
    next_action = flow.get('next_action') or ''
    # The producer's unknown-native fallback is diagnostic context, not an
    # authored action for a durable wait/result. Match only that known default;
    # a concrete action or dependency remains authoritative, even with unknown
    # native activity. Keep the original flow untouched for inspection.
    if (flow.get('phase') == 'unknown' and report_state
            and phase in ('waiting', 'reported', 'technical', 'dependency')
            and next_action == 'Firstmate necesita una observación actual; la última declaración se conserva como contexto.'):
        next_action = ''
    return {'phase': phase, 'label': labels[phase], 'reason': reason or tr('No specific reason was recorded.', 'No se registró un motivo concreto.'),
            'waiting_for': flow.get('waiting_for') or '',
            'next_action': next_action or actions[phase],
            'firstmate_action': supervision,
            'report_state': report_state,
            'native_status': (tr('Unknown native state', 'Estado nativo desconocido') if getattr(card, 'activity_unconfirmed', False)
                              else tr('Firstmate state: ', 'Estado de Firstmate: ') + (state or tr('unknown', 'desconocido')))}


VALIDATION_RE = re.compile(r"validating \((?:running|fixing)\)")


def card_activity(card: Card) -> dict[str, object]:
    """What is happening to a card right now, from structured fields only.

    Firstmate's current state is the fm-crew-state verdict. Its source tells an
    agent's own busy pane from a validation run reported as working for it;
    any other source (status log, remote endpoint, none) is not live evidence.
    Endpoint presence is Firstmate's backend probe: the agent's pane exists,
    not that an agent runs in it. Hold and pause dates are used only as
    exported fields; reason prose is never parsed for them. A hold day is
    compared with the local calendar day; a pause time without an offset is
    UTC. A date that has passed is never shown as the end. A snapshot kept
    after its refresh failed never claims work now.

    kind: working|validating|last_seen_working|validation_gate|blocked|decision|
          paused|deferred|held|finished|integration|technical|stopped|ready|
          queued|delivered|unknown.
    agent: working|validation|open (pane open; working reported without a
           live source)|present (pane open, not reported working)|none|
           unknown (Underway only).
    until: the exported date for paused/deferred. gap: the missing producer
    field that would have made the card more specific. seen_at: receipt time
    of the retained snapshot for last_seen_working.
    """
    flow = getattr(card, "flow", {}) or {}
    state = getattr(card, "snapshot_state", "") or ""
    confirmed = not getattr(card, "activity_unconfirmed", True)
    working_verdict = state == "working" and confirmed
    retained_at = getattr(card, "retained_at", 0.0) or 0.0
    source = getattr(card, "state_source", "") or ""
    flow_source = flow.get("source") if isinstance(flow.get("source"), str) else ""
    if not source and flow_source.startswith("current_state."):
        source = flow_source.split(".", 1)[1]
    run = getattr(card, "run_step", {}) or {}
    run_live = (run.get("phase") in ("validation", "ci") and run.get("freshness", "fresh") == "fresh"
                and run.get("state") in ("running", "fixing", "pending", "ci"))
    reason = flow.get("reason") if isinstance(flow.get("reason"), str) else ""
    validation = source == "run-step" or (not source and (run_live or bool(VALIDATION_RE.match(reason or card.doing or ""))))
    deps = [d for d in getattr(card, "dependencies", []) or [] if d.get("unresolved") is not False]
    blocked = bool(deps) or card.blocked_by not in ("", "-") or flow.get("phase") == "dependency"
    hold_bucket = getattr(card, "hold_bucket", "") or ""
    now = datetime.now(timezone.utc)
    today = now.astimezone().date()
    try:
        hold_day = date.fromisoformat(getattr(card, "hold_until", "") or "")
    except (TypeError, ValueError):
        hold_day = None
    deferred = hold_day is not None and hold_day > today and hold_bucket in ("dated", "")
    try:
        wait_at = datetime.fromisoformat((getattr(card, "wait_until", "") or "").replace("Z", "+00:00"))
        wait_at = wait_at if wait_at.tzinfo else wait_at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        wait_at = None
    report_state = durable_report(card)[0]
    exists = getattr(card, "endpoint_exists", None)
    result = {"kind": "unknown", "agent": "", "until": "", "gap": "", "seen_at": 0.0}
    if card.bucket == "charted":
        if hold_bucket == "blocked" or blocked:
            result["kind"] = "blocked"
        elif deferred:
            result.update(kind="deferred", until=hold_day.isoformat())
        elif hold_bucket in ("dated", "aged") or flow.get("phase") == "human" or flow.get("state") == "held":
            # A captain hold outside Your decision is not asking the person now.
            result.update(kind="held", gap="" if hold_bucket else "hold_until")
        else:
            result["kind"] = "ready" if flow.get("state") == "ready" else "queued"
        return result
    if card.bucket != "underway":
        result["kind"] = "decision" if card.bucket == "captains_call" else "delivered"
        return result
    if working_verdict and (source == "pane" or validation):
        if retained_at:
            result.update(kind="last_seen_working", seen_at=retained_at)
        else:
            result["kind"] = "validating" if validation else "working"
    elif state == "parked" and validation:
        result["kind"] = "validation_gate"
    elif blocked:
        result["kind"] = "blocked"
    elif flow.get("phase") == "human":
        result["kind"] = "decision"
    elif wait_at is not None:
        result.update(kind="paused", until=wait_at.isoformat() if wait_at > now else "")
    elif deferred:
        result.update(kind="deferred", until=hold_day.isoformat())
    elif hold_bucket == "dated":
        result["kind"] = "held"
    elif state == "done" and confirmed:
        result["kind"] = "integration"
    elif report_state == "done":
        result["kind"] = "finished"
    elif state in ("paused", "parked") or report_state in ("paused", "parked"):
        result.update(kind="paused", gap="wait_until")
    elif state in ("blocked", "failed") or report_state in ("blocked", "failed"):
        result["kind"] = "technical"
    elif exists is False:
        result["kind"] = "stopped"
    if result["kind"] == "working":
        result["agent"] = "working"
    elif result["kind"] == "validating":
        result["agent"] = "validation"
    else:
        claimed = working_verdict and source in ("", "pane", "run-step")
        result["agent"] = {True: "open" if claimed else "present", False: "none"}.get(exists, "unknown")
    return result


def activity_counts(cards: list[Card]) -> dict[str, int]:
    """Per-kind totals; ``working`` counts agents Firstmate reports working now."""
    counts: dict[str, int] = {}
    for card in cards:
        kind = card_activity(card)["kind"]
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def dependency_caption(card: Card) -> str:
    deps = getattr(card, 'dependencies', [])
    if not deps:
        return card.blocked_by if card.blocked_by not in ('', '-') else ''
    def owner_label(dep):
        owner = dep.get('owner')
        return (getattr(card, 'owner_label', '') or 'Firstmate') if owner == '(main)' else owner or 'responsable no registrado'
    return '; '.join(f"{d.get('title') or d['id']} · {owner_label(d)}" + (' (resuelta)' if d.get('unresolved') is False else '') + (' (vínculo por conciliar)' if d.get('reconciliation_required') is True else '')
                     for d in deps)


def explanation_request(home: Home, task: str, *, status: bool = False, translate=None) -> dict:
    """Identity-only intake. A stable key delegates deduplication to Firstmate."""
    if is_remote_home(home) or not delivery_home_identity(home) or not KEY_RE.fullmatch(task):
        return {'state': 'unsupported', 'message': 'Este home no permite solicitar explicaciones desde Deck.'}
    tr = translate or _presentation_text
    script = endpoint_path(home.path, 'fm-delivery-explain.sh')
    if not os.path.isfile(script):
        return {'state': 'unsupported', 'message': tr('This backend does not offer the explanation intake yet. Firstmate must update this home.', 'Este backend aún no ofrece el intake de explicación. Firstmate debe actualizar ese home.')}
    request_id = 'deck-explain-' + delivery_key(delivery_home_identity(home), task)[:32]
    command = [script, '--task', task, '--request-id', request_id, '--json']
    if status:
        command.append('--status')
    try:
        env = _child_env()
        env['FM_HOME'] = home.path
        result = subprocess.run(shell_command(*command), env=env, capture_output=True, text=True,
                                timeout=8, stdin=subprocess.DEVNULL)
        data = json.loads(result.stdout)
        if (result.returncode not in (0, 3) or (result.returncode == 3 and data.get('state') != 'saved_unannounced') or not data.get('ok') or data.get('state') not in
                ('queued', 'acknowledged', 'answered', 'saved_unannounced', 'absent') or
                data.get('request_id') != request_id or data.get('task') != task or
                os.path.realpath(data.get('home', '')) != os.path.realpath(home.path)):
            raise ValueError(tr('invalid response or different identity', 'respuesta inválida o identidad diferente'))
        return data
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        return {'state': 'error', 'request_id': request_id, 'message': tr('Could not verify the request: {error}. Retrying uses the same identifier.', 'No se pudo verificar la solicitud: {error}. Reintentar usa el mismo identificador.', error=exc)}


class UI:
    def __init__(self) -> None:
        self.collector = Collector()
        self.show_landed = show_landed_default()
        # Session-only view state: the collector leaves answered calls off the
        # board unless this is on, because they leave and return with the flag.
        self.reveal_answered = False
        self.columns = self.board_columns(None)
        self.col_idx = 0
        self.selection_auto = True
        self.card_idx = 0
        self.scroll: dict[str, int] = {}
        self.flash = ""
        self.flash_at = 0.0
        self.tab_regions: list[tuple[int, int, str]] = []
        self.card_regions: list[tuple[int, int, int, int, Card]] = []
        self.last_seq = -1
        self.last_render = 0.0
        self.width = 120
        self.height = 40
        self.colw = 24
        self.visible_column_indices = []
        self.body_h = 32
        self.pending_home = ""
        self.dirty = False
        self.last_frame: list[str] | None = None
        self.last_size: tuple[int, int] = (0, 0)
        self.quitting = False
        self.dialog: DecisionDialog | None = None
        self.dialog_hit: list[tuple[int, int, int, str, int]] = []
        self.dialog_box: tuple[int, int, int, int] | None = None
        self.dialog_scroll = 0
        self._dialog_scroll_follow = False
        self.help = False
        self.help_box: tuple[int, int, int, int] | None = None
        self.message: MessageBox | None = None
        self.delivery = None
        self.delivery_scroll = 0
        self.delivery_max = 0
        self.delivery_page_rows = 1
        self.delivery_card = None
        self.delivery_views = None
        self.delivery_view = 'summary'
        self.delivery_context = None
        self.delivery_event = None
        self.explanation = None
        self.explanation_notice = False
        self.delivery_action_focus = 0
        self.delivery_actions = ['summary', 'report', 'details', 'back', 'explain', 'status']
        self.history = None
        self.history_hits = []
        self.reader_hits = []
        self.details: Card | None = None
        self.details_scroll = 0
        self.details_max = 0
        self.footer_hits: list[tuple[int, int, str]] = []

    # -- helpers ------------------------------------------------------------
    def say(self, msg: str) -> None:
        self.flash = msg
        self.flash_at = time.time()
        self.dirty = True

    def current_key(self) -> str:
        self.col_idx = max(0, min(self.col_idx, len(self.columns) - 1))
        return self.columns[self.col_idx][0] if self.columns else ""

    def board_columns(self, home: Home | None) -> list[tuple[str, str]]:
        """All retains real-home deliveries and adds Other Agents."""
        visible = [c for c in COLUMNS if c[0] != 'awaiting_merge' and (self.show_landed or c[0] != 'landed')]
        if is_aggregate_home(home):
            return visible + [OTHER_AGENTS_COLUMN]
        return visible

    def toggle_landed(self) -> None:
        self.show_landed = not self.show_landed
        self.selection_auto = not self.show_landed
        self.columns = self.board_columns(self.collector.snapshot().home)
        if self.show_landed:
            for i, (key, _) in enumerate(self.columns):
                if key == "landed":
                    self.col_idx = i
                    self.card_idx = 0
                    break
        else:
            self.col_idx = max(0, min(self.col_idx, len(self.columns) - 1))
        set_show_landed_pref(self.show_landed)
        self.dirty = True
        self.say(f"landed column {'shown' if self.show_landed else 'hidden'}")

    def toggle_answered(self) -> None:
        """Show or hide the Captain's Call rows the captain already answered."""
        self.reveal_answered = not self.reveal_answered
        self.collector.set_reveal_answered(self.reveal_answered)
        self.dirty = True
        self.say(f"answered calls {'shown' if self.reveal_answered else 'hidden'}")

    def crew_tab_count(self, snap: Snapshot, home_label: str) -> int | None:
        if home_label == ALL_CREW_LABEL:
            count = snap.counts.get(ALL_CREW_LABEL)
            return count if count is not None else None
        count = snap.counts.get(home_label)
        if count is None:
            return None
        if self.show_landed:
            return count
        return max(0, count - snap.landed_counts.get(home_label, 0))

    def current_cards(self) -> list[Card]:
        snap = self.collector.snapshot()
        key = self.current_key()
        return snap.cols.get(key) or []

    def selected(self) -> Card | None:
        cards = self.current_cards()
        if not cards:
            return None
        self.card_idx = min(self.card_idx, len(cards) - 1)
        return cards[self.card_idx]

    def open_details(self) -> None:
        if self.selected() is not None and self.selected().bucket == "landed":
            self.open_delivery(self.selected())
            return
        self.details = self.selected()
        self.details_scroll = 0
        self.dirty = True

    def details_scroll_by(self, delta: int) -> None:
        self.details_scroll = min(self.details_max, max(0, self.details_scroll + delta))
        self.dirty = True

    # -- focus action -------------------------------------------------------
    def activate(self) -> None:
        """Enter / click on a ticket: a Captain's Call ticket opens its decision
        dialog; a Landed ticket opens its delivery; other tickets open full context; o focuses a pane."""
        card = self.selected()
        if card is None:
            self.say("no ticket in this column")
            return
        if card.bucket == "captains_call":
            self.open_decision(card)
            return
        if card.bucket == "landed":
            self.open_delivery(card)
            return
        if card.bucket == OTHER_AGENTS_COLUMN[0]:
            self.open_selected()
        else:
            self.open_details()

    def open_selected(self) -> None:
        card = self.selected()
        if card is None:
            self.say("no ticket in this column")
            return
        self.open_card_pane(card)

    def open_card_pane(self, card: Card) -> None:
        snap = self.collector.snapshot()
        if card.bucket == "landed" and card.pane_id not in snap.panes:
            self.say(f"{card.task}: no live pane; Enter opens delivery")
            return
        tab = card.tab_id
        if not tab and card.pane_id:
            tab = (snap.panes.get(card.pane_id) or {}).get("tab_id", "")
        if tab and self._focus([HERDR_BIN, "tab", "focus", tab]):
            self.say(f"opened {card.task} \u2192 {tab}")
            return
        if card.workspace_id and self._focus([HERDR_BIN, "workspace", "focus", card.workspace_id]):
            self.say(f"opened {card.task} \u2192 workspace {card.workspace_id}")
            return
        wt = card.worktree or "(no worktree yet)"
        self.say(f"{card.task}: no live pane \u00b7 {wt}")

    @staticmethod
    def _focus(cmd: list[str]) -> bool:
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=10, env=_child_env(), stdin=subprocess.DEVNULL)
            return p.returncode == 0
        except Exception:
            return False

    # -- read-only deliveries and retained history -------------------------
    def open_history(self) -> None:
        snap = self.collector.snapshot()
        home = snap.home
        if home is None:
            self.say("no home selected")
            return
        if is_aggregate_home(home):
            rows, notes = [], []
            for source in real_homes(snap.homes):
                source_rows, source_note = delivery_records(source)
                rows.extend(source_rows)
                notes.append(source.label + ': ' + source_note)
            rows.sort(key=lambda r: (r.get('closed', ''), r['id'], r['delivery_identity']), reverse=True)
            note = '\n'.join(notes) or 'History unavailable: no discovered real homes; choose the owning crew tab.'
        else:
            rows, note = delivery_records(home)
        self.history = {"home": home, "rows": rows, "note": note, "query": "", "index": 0}
        self.dirty = True

    def history_rows(self) -> list[dict]:
        state = self.history
        if state is None:
            return []
        def normalized(text):
            return ''.join(ch for ch in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(ch))
        terms = normalized(state['query']).split()
        return [r for r in state['rows'] if all(term in normalized(' '.join(
            str(r.get(key, '')) for key in ('id', 'title', 'body', 'closed', 'verb', 'artifact', 'source', 'owner_label')
        )) for term in terms)]

    def history_move(self, delta: int) -> None:
        rows = self.history_rows()
        self.history["index"] = min(max(0, len(rows) - 1), max(0, self.history["index"] + delta))
        self.dirty = True

    def history_info(self) -> None:
        self.delivery_card = None
        self.delivery_views = None
        self.delivery_context = None
        self.delivery_view = 'info'
        self.delivery_action_focus = 0
        self.delivery_actions = ['back']
        self.delivery_scroll = 0
        self.delivery = ("Retained delivery history · sources and controls\n\n" +
                         delivery_text(self.history["note"]) + "\n\nFull search text: " + self.history["query"] +
                         "\n\nType to search task id, full title, canonical notes, date, artifact or source. "
                         "Arrows select; Page Up/Down page; Home/End first/last; Enter/click opens. "
                         "Ctrl+U or Clear resets search. Esc or Back returns. Tab or Info opens this source detail. "
                         "Opening a delivery marks it read locally; searching, refresh and this source detail do not. "
                         "Read is not acceptance or approval.\n\n" +
                         "Delivery sheets: arrows/j/k, Page Up/Down, Home/End or wheel scroll. "
                         "Up/Down buttons page; Back/Esc returns; o/Pane focuses a verified live pane when available. "
                         "Missing reports and backend exports do not create automatic messages or calls.")
        self.dirty = True

    def history_open(self) -> None:
        rows = self.history_rows()
        if rows:
            self.history["index"] = min(self.history["index"], len(rows) - 1)
            self.open_delivery(rows[self.history["index"]], self.history["home"])

    def open_delivery(self, card, home: Home | None = None) -> None:
        snap = self.collector.snapshot()
        self.delivery_card = None
        self.delivery_views = None
        self.delivery_context = None
        self.delivery_view = 'summary'
        self.delivery_action_focus = 0
        self.delivery_actions = ['back']
        self.explanation_notice = False
        note = ""
        if isinstance(card, Card):
            self.delivery_card = card
            identity = getattr(card, 'delivery_identity', '')
            matches = [h for h in real_homes(snap.homes) if identity and delivery_home_identity(h) == identity]
            home = matches[0] if len(matches) == 1 else None
            problem = getattr(card, 'delivery_problem', '')
            if home is not None:
                resolved, owner_problem = delivery_owner_home(snap.homes, home, card.owner, card.id)
                if resolved is None or delivery_home_identity(resolved) != identity:
                    problem = owner_problem or 'Delivery owner unavailable: retained ownership conflicts with this card; refresh the owning crew tab.'
            if home is None or problem:
                self.delivery = delivery_text(problem or 'Delivery owner unavailable: reopen from the owning crew tab; its discovered identity is missing or ambiguous.') + '\n' + delivery_text(card.title)
                self.delivery_scroll = 0
                self.dirty = True
                return
            rows, note = delivery_records(home, task_id=card.task)
            record = next((r for r in rows if r["id"] == card.task), None)
            if record is None:
                record = {"id": card.task, "what": card.title, "artifact": card.artifact,
                          "source": "recent snapshot; canonical record unavailable (text may be abbreviated)"}
        else:
            record = card
            identity = record.get('delivery_identity', '')
            matches = [h for h in real_homes(snap.homes) if identity and delivery_home_identity(h) == identity]
            home = matches[0] if len(matches) == 1 else None
            if home is None:
                self.delivery = 'Delivery owner unavailable: reopen history from the owning crew tab; its discovered identity is missing or ambiguous.'
                self.delivery_scroll = 0
                self.dirty = True
                return
            self.delivery_card = next((c for cards in snap.cols.values() for c in cards
                                       if c.task == record["id"] and c.delivery_identity == identity), None)
        board_event = getattr(card, 'event_time', None) if isinstance(card, Card) else None
        content = delivery_content(record, home, board_event, split_views=True)
        if isinstance(content, dict):
            self.delivery_views = content
            self.delivery = content['summary']
        else:
            self.delivery = readable_markdown(content)
        self.delivery_context = (home, record['id'])
        self.delivery_actions = (['summary', 'report', 'details', 'back'] if self.delivery_views else ['back']) + ['explain', 'status']
        self.delivery_event = producer_card_time(record.get('card_time'), 'landed', CardTime('Delivered', record.get('closed')))
        if board_event is not None and board_event.epoch is not None:
            self.delivery_event = board_event
        self.explanation = explanation_request(home, record['id'], status=True)
        if note:
            if self.delivery_views:
                self.delivery_views['details'] += "\n\nFuentes del historial: " + delivery_text(note)
        warning = delivery_mark_read(delivery_home_identity(home), record["id"])
        if warning:
            self.delivery += "\n\n" + warning
        self.delivery_scroll = 0
        self.dirty = True

    def delivery_scroll_by(self, delta: int) -> None:
        self.delivery_scroll = min(self.delivery_max, max(0, self.delivery_scroll + delta))
        self.dirty = True

    def delivery_report(self) -> None:
        """Jump to full retained report text without launching URL handlers."""
        if self.delivery_views:
            self.delivery_set_view('report')
            return
        inner = max(4, min(122, self.width - 2) - 4)
        offset = 0
        for line in self.delivery.splitlines():
            if line.startswith('Full report: data/'):
                self.delivery_scroll = min(offset, self.delivery_max)
                self.dirty = True
                return
            offset += len(wrap_text(line, inner))
        self.say('Full report unavailable; see retained sources and details.')

    def delivery_set_view(self, view: str) -> None:
        if self.delivery_views:
            self.delivery_view = view
            self.delivery = self.delivery_views[view]
            self.delivery_scroll = 0
            self.dirty = True

    def delivery_explain(self, *, status: bool = False) -> None:
        if self.delivery_context is None:
            return
        home, task = self.delivery_context
        # The backend is authoritative for both state and idempotency. Repeated
        # clicks retain the same request ID, including uncertain transport errors.
        already_sent = self.explanation and self.explanation.get('state') in ('queued', 'acknowledged', 'answered', 'saved_unannounced')
        self.explanation = explanation_request(home, task, status=status or bool(already_sent))
        self.explanation_notice = True
        self.delivery_scroll = 0
        self.dirty = True

    def delivery_back(self) -> None:
        if self.delivery_views and self.delivery_view != 'summary':
            self.delivery_set_view('summary')
        else:
            self.delivery = None
            self.dirty = True

    def reader_frame(self, lines, w, h, title, rows, footer):
        """Small terminals retain content scroll plus a fixed action row."""
        dw = max(8, min(122, w - 2))
        inner = dw - 4
        footers = footer if isinstance(footer, list) else [footer]
        budget = max(1, h - 4 - len(footers))
        x0 = max(0, (w - dw) // 2)
        box = ["╭" + pad(clip(title, dw - 2), dw - 2) + "╮"]
        for row in rows[:budget]:
            body = pad(clip(row, inner), inner)
            if row.casefold() in ('resultado', 'alcance', 'por qué', 'cómo', 'entregado', 'verificado', 'evidencia', 'pendiente'):
                body = BOLD + fg(C_ACCENT) + body + RESET
            box.append('│ ' + body + ' │')
        while len(box) < budget + 1:
            box.append("│ " + " " * inner + " │")
        box += ["│ " + pad(clip(row, inner), inner) + " │" for row in footers]
        box += ["╰" + "─" * (dw - 2) + "╯"]
        y0 = max(0, (h - len(box)) // 2)
        for i, row in enumerate(box):
            if y0 + i < len(lines):
                lines[y0 + i] = " " * x0 + row + " " * max(0, w - x0 - dw)
        return x0 + 2, y0, budget, inner

    def render_delivery(self, lines, w, h):
        inner = max(4, min(122, w - 2) - 4)
        controls = [('summary', 'Resumen'), ('report', 'r Informe'), ('details', 'd Detalles'), ('back', 'Volver')]
        if not self.delivery_views:
            controls = [('back', 'Volver')]
        if self.delivery_context:
            controls += [('explain', 'e Pedir explicación a Firstmate'), ('status', 's Estado')]
        self.delivery_actions = [action for action, _ in controls]
        self.delivery_action_focus = min(self.delivery_action_focus, len(controls) - 1)
        action_rows, hits = [''], []
        for i, (action, label) in enumerate(controls):
            token = ('>' if i == self.delivery_action_focus else '') + '[' + label + ']'
            if action_rows[-1] and display_width(action_rows[-1] + ' ' + token) > inner:
                action_rows.append('')
            x = display_width(action_rows[-1]) + (1 if action_rows[-1] else 0)
            action_rows[-1] += (' ' if action_rows[-1] else '') + token
            hits.append((x, x + display_width(token), len(action_rows) - 1, action))
        heading = []
        if self.delivery_context:
            home, task = self.delivery_context
            heading = [local_time_caption(self.delivery_event), '']
            if self.delivery_view == 'details' or self.explanation_notice:
                heading.insert(0, f'{home.label} · {task}')
        status_rows = []
        if self.delivery_context and self.explanation and (self.explanation_notice or self.explanation.get('state') not in ('absent', 'unsupported')):
            states = {'queued': 'En cola para Firstmate', 'acknowledged': 'Firstmate confirmó recepción',
                      'answered': 'Firstmate respondió; consulta Detalles', 'saved_unannounced': 'Guardada; aviso a Firstmate no confirmado',
                      'absent': 'Sin solicitud', 'unsupported': 'Explicación no disponible en este backend',
                      'error': 'Solicitud sin verificar; s consulta el estado'}
            state = self.explanation.get('state', 'error')
            status_rows += wrap_text('Solicitud: ' + states[state], inner)
            if state in ('error', 'unsupported', 'saved_unannounced'):
                status_rows += wrap_text(delivery_text(self.explanation.get('message', '')), inner)
            if self.delivery_view == 'details':
                self_detail = 'Solicitud verificable: ' + delivery_text(json.dumps(self.explanation, ensure_ascii=False))
            else:
                self_detail = ''
        else:
            self_detail = ''
        rows = [row for line in (heading + status_rows + [''] + self.delivery.splitlines() + ([self_detail] if self_detail else [])) for row in wrap_text(line, inner)]
        budget = max(1, h - 4 - len(action_rows))
        self.delivery_page_rows = budget
        self.delivery_max = max(0, len(rows) - budget)
        self.delivery_scroll = min(self.delivery_scroll, self.delivery_max)
        start = self.delivery_scroll
        label = {'summary': 'Resumen', 'report': 'Informe técnico', 'details': 'Detalles y fuentes'}.get(self.delivery_view, 'Historial')
        title = f" Entrega · {label} · {start + 1}-{min(len(rows), start + budget)}/{len(rows)} "
        x, y, budget, _ = self.reader_frame(lines, w, h, title, rows[start:start + budget], action_rows)
        self.reader_hits = [(x + a, x + b, y + budget + 1 + row, action) for a, b, row, action in hits]

    def delivery_action(self, action: str) -> None:
        if action == 'back':
            self.delivery_back()
        elif action in ('summary', 'report', 'details'):
            self.delivery_set_view(action)
        elif action in ('explain', 'status'):
            self.delivery_explain(status=action == 'status')

    def render_history(self, lines, w, h):
        state = self.history
        rows = self.history_rows()
        state["index"] = min(state["index"], max(0, len(rows) - 1))
        inner = max(4, min(122, w - 2) - 4)
        budget = max(1, h - 5)
        content = wrap_text("Search: " + state["query"] + "_", inner)
        limited = any(word in state["note"].lower() for word in ("unavailable", "incomplete", "missing"))
        content += wrap_text("Retenido: " + ("fuentes incompletas" if limited else "actual + archivo") + " · Tab/Info: retención y fuentes", inner)
        content += wrap_text("Type search · Enter open · ↑↓ select · Esc back", inner)
        # Source details are fully scrollable through Tab/Info even when there
        # are no rows (offline/backend missing), without marking anything read.
        content = content[:max(1, min(4, budget - 2))]
        mapping = []
        index = state["index"]
        for i in range(index, len(rows)):
            record = rows[i]
            unread = "new" if not delivery_seen(record['delivery_identity'], record["id"]) else "read"
            label = f"{'>' if i == index else ' '} {unread} · {record.get('closed') or '?'} · {record['id']} · {record['source']}"
            if is_aggregate_home(state['home']):
                label += ' · ' + record['owner_label']
            entry = [delivery_text(label)] + wrap_text(delivery_text(record["title"]), inner)
            for row in entry:
                if len(content) >= budget:
                    break
                mapping.append((len(content), i))
                content.append(row)
            if len(content) >= budget:
                break
        if not rows:
            content.append("No matching retained deliveries.")
        title = f" Delivery history · {state['home'].label} · {len(rows)} matches · {index + 1 if rows else 0}/{len(rows)} "
        actions = "[Prev] [Next] [Open] [Clear] [Back] [Info]"
        x, y, budget, _ = self.reader_frame(lines, w, h, title, content, actions)
        self.history_hits = [(x, x + inner, y + 1 + row, i) for row, i in mapping]
        self.reader_hits = [(x, x + 6, y + budget + 1, "prev"),
                            (x + 7, x + 13, y + budget + 1, "next"),
                            (x + 14, x + 20, y + budget + 1, "open"),
                            (x + 21, x + 28, y + budget + 1, "clear"),
                            (x + 29, x + 35, y + budget + 1, "back"),
                            (x + 36, x + 42, y + budget + 1, "info")]

    def reader_click(self, x: int, y: int, button: int) -> None:
        if button in (64, 65, 68, 69):
            delta = -WHEEL_ROWS if button in (64, 68) else WHEEL_ROWS
            if self.delivery is not None:
                self.delivery_scroll_by(delta)
            else:
                self.history_move(delta)
            return
        if button != 0:
            return
        for x1, x2, row, action in self.reader_hits:
            if row == y and x1 <= x < x2:
                if action == "back":
                    if self.delivery is not None:
                        self.delivery_back()
                    else:
                        self.history = None
                elif action == "pane":
                    if self.delivery_card is not None:
                        self.open_card_pane(self.delivery_card)
                    else:
                        self.say("no live pane for this delivery")
                elif action == "report":
                    self.delivery_report()
                elif self.delivery is not None and action in ('summary', 'details', 'explain', 'status'):
                    self.delivery_action(action)
                elif action in ("up", "down"):
                    self.delivery_scroll_by(-max(1, self.height - 5) if action == "up" else max(1, self.height - 5))
                elif action in ("prev", "next"):
                    self.history_move(-1 if action == "prev" else 1)
                elif action == "clear":
                    self.history["query"] = ""
                    self.history["index"] = 0
                elif action == "info":
                    self.history_info()
                elif action == "open":
                    self.history_open()
                return
        if self.delivery is None:
            for x1, x2, row, index in self.history_hits:
                if y == row and x1 <= x < x2:
                    self.history["index"] = index
                    self.history_open()
                    return

    # -- captain's call dialog ---------------------------------------------
    def open_decision(self, card: Card) -> None:
        snap = self.collector.snapshot()
        if is_remote_home(snap.home):
            # Remote homes stay read-only on the board: answering a call runs
            # the local home's intake and steer, which cannot reach that home.
            self.say("remote home \u00b7 read-only on the board")
            return
        content = decision_card_content(card, snap.homes, snap.home, bearings=snap.bearings)
        self.dialog = DecisionDialog(content)
        self.dialog_hit = []
        self.dialog_box = None
        self.dialog_scroll = 0
        self._dialog_scroll_follow = False
        self.say(f"captain's call: {card.task}")
        self.dirty = True

    def close_dialog(self) -> None:
        if self.dialog is not None and not self.dialog.busy:
            self.dialog = None
            self.dirty = True

    def dialog_move(self, delta: int) -> None:
        d = self.dialog
        if d is None or not d.options:
            return
        d.cursor = max(0, min(len(d.options) - 1, d.cursor + delta))
        d.focus = "options"
        self.dirty = True

    def dialog_pick(self, index: int | None = None) -> None:
        """Space / click on an option row: radio-select it, or clear it when it
        is already the selection so a freeform-only answer stays possible."""
        d = self.dialog
        if d is None or not d.options:
            return
        i = d.cursor if index is None else max(0, min(len(d.options) - 1, index))
        d.cursor = i
        d.selected = -1 if d.selected == i else i
        d.focus = "options"
        self.dirty = True

    def dialog_type(self, text: str) -> None:
        d = self.dialog
        if d is None:
            return
        if not d.freeform:
            self.say("this call takes a listed option")
            return
        d.note += text
        d.focus = "note"
        self._dialog_scroll_follow = True
        self.dirty = True

    def dialog_backspace(self) -> None:
        d = self.dialog
        if d is None:
            return
        d.note = d.note[:-1]
        self._dialog_scroll_follow = True
        self.dirty = True

    def dialog_scroll_by(self, delta: int) -> None:
        if self.dialog is None:
            return
        self.dialog_scroll = max(0, self.dialog_scroll + delta)
        self._dialog_scroll_follow = False
        self.dirty = True

    def dialog_toggle_focus(self) -> None:
        d = self.dialog
        if d is None:
            return
        if d.focus == "options":
            d.focus = "note"
            self._dialog_scroll_follow = True
        else:
            d.focus = "options"
        self.dirty = True

    def dialog_submit(self) -> None:
        d = self.dialog
        if d is None or d.busy or d.queued:
            return
        answer = d.display_answer()
        if not answer:
            d.error = "choose an option or type an answer"
            d.busy = False
            self.dirty = True
            return
        if len(answer.encode("utf-8")) > ANSWER_LIMIT:
            d.error = f"answer is too long ({ANSWER_LIMIT} bytes maximum)"
            self.dirty = True
            return
        d.error = ""
        d.busy = True
        d.focus = "options"
        self.dirty = True
        threading.Thread(target=self._submit_worker, args=(d,), daemon=True).start()

    def _submit_worker(self, d: DecisionDialog) -> None:
        ok, detail = run_submit(d)
        if ok:
            d.queued = True
            d.busy = False
            d.done_at = time.time()
            # a wake problem is shown beside the queued state: the answer is
            # already durable, so it must not read as a failed submit
            d.error = detail
            self.say(f"answer queued: {d.key}")
        else:
            d.error = detail or "the answer intake refused this call"
            d.busy = False
        self.dirty = True

    def _click_dialog(self, x: int, y: int, button: int) -> None:
        d = self.dialog
        if d is None:
            return
        if button in (64, 65, 68, 69):
            delta = -WHEEL_ROWS if button in (64, 68) else WHEEL_ROWS
            if self.dialog_scroll > 0 or getattr(self, "_dialog_scroll_max", 0) > 0:
                self.dialog_scroll_by(delta)
            else:
                self.dialog_move(-1 if button in (64, 68) else 1)
            return
        for y1, x1, x2, kind, idx in self.dialog_hit:
            if y1 == y and x1 <= x < x2:
                if kind == "option":
                    self.dialog_pick(idx)
                elif kind == "button":
                    self.dialog_submit()
                elif kind == "note":
                    d.focus = "note"
                    self._dialog_scroll_follow = True
                    self.dirty = True
                return
        box = self.dialog_box
        if box and box[0] <= x < box[2] and box[1] <= y < box[3]:
            return
        self.close_dialog()

    def switch_home(self, label: str) -> None:
        # Optimistic: highlight the tab and show loading state immediately; the
        # collector paints cached cards first and re-runs bearings after.
        self.pending_home = label
        self.collector.set_active(label)
        self.col_idx = 0
        self.selection_auto = True
        self.card_idx = 0
        self.scroll.clear()
        self.say(f"crew: {label}")

    def scroll_by(self, key: str, delta: int) -> None:
        self.scroll[key] = max(0, self.scroll.get(key, 0) + delta)
        self.dirty = True
        self._anchor_selection(key)

    def scroll_selected(self, delta: int) -> None:
        self.scroll_by(self.current_key(), delta)

    def scroll_bound(self, bottom: bool = False) -> None:
        key = self.current_key()
        cards = self.collector.snapshot().cols.get(key) or []
        self.scroll[key] = len(cards) * CARD_ROWS if bottom else 0
        self.dirty = True
        self._anchor_selection(key)

    def _anchor_selection(self, key: str) -> None:
        """Keep the selected card inside the visible rows of a column that was
        just scrolled, so render() does not pull the offset back."""
        if key != self.current_key():
            return
        cards = self.collector.snapshot().cols.get(key) or []
        if not cards:
            return
        off = self.scroll.get(key, 0)
        body_h = max(8, self.body_h)
        first = min(len(cards) - 1, off // CARD_ROWS)
        last = min(len(cards) - 1, (off + body_h - 1) // CARD_ROWS)
        self.card_idx = min(max(self.card_idx, first), last)
    def next_home(self, delta: int) -> None:
        snap = self.collector.snapshot()
        if not snap.homes:
            return
        labels = [h.label for h in snap.homes]
        try:
            i = labels.index(self.collector._active)
        except ValueError:
            i = 0
        self.switch_home(labels[(i + delta) % len(labels)])

    # -- mouse --------------------------------------------------------------
    def click(self, x: int, y: int, button: int) -> None:
        _debug(f"click x={x} y={y} button={button} regions={len(self.card_regions)}")
        self.dirty = True
        if self.delivery is not None or self.history is not None:
            self.reader_click(x, y, button)
            return
        if self.message is not None:
            return  # the `m` box takes keys only; Esc closes it
        if self.details is not None:
            if button in (64, 65, 68, 69):
                self.details_scroll_by(-WHEEL_ROWS if button in (64, 68) else WHEEL_ROWS)
            return
        if self.help:
            if button in (64, 65, 68, 69):
                return
            box = self.help_box
            if not (box and box[0] <= x < box[2] and box[1] <= y < box[3]):
                self.close_help()
            return
        if self.dialog is not None:
            self._click_dialog(x, y, button)
            return
        if button in (64, 65, 68, 69):  # wheel; shift+wheel = 68/69 (all columns)
            delta = (-WHEEL_ROWS if button in (64, 68) else WHEEL_ROWS)
            if button in (68, 69):
                for key, _ in self.columns:
                    self.scroll_by(key, delta)
            else:
                visible = self.visible_column_indices or list(range(len(self.columns)))
                ci = visible[min(len(visible) - 1, max(0, x // max(1, self.colw + 1)))]
                self.scroll_by(self.columns[ci][0], delta)
            return
        if y == self.height - 1:
            for x1, x2, action in self.footer_hits:
                if x1 <= x < x2:
                    if action == "help":
                        self.open_help()
                    elif action == "details":
                        self.open_details()
                    elif action == "history":
                        self.open_history()
                    elif action == "landed":
                        self.toggle_landed()
                    elif action == "answered":
                        self.toggle_answered()
                    elif action == "refresh":
                        self.collector.refresh_now()
                        self.say("refreshing\u2026")
                    return
        for x1, x2, label in self.tab_regions:
            if y == 0 and x1 <= x < x2:
                self.switch_home(label)
                return
        for x1, y1, x2, y2, card in self.card_regions:
            if x1 <= x < x2 and y1 <= y < y2:
                _debug(f"hit card {card.task} region=({x1},{y1},{x2},{y2})")
                snap = self.collector.snapshot()
                for ci, (key, _) in enumerate(self.columns):
                    cards = snap.cols.get(key) or []
                    if card in cards:
                        self.col_idx = ci
                        self.card_idx = cards.index(card)
                        break
                self.activate()
                return

    # -- render -------------------------------------------------------------
    def render(self, snap: Snapshot, force: bool = False) -> None:
        try:
            size = shutil.get_terminal_size((120, 40))
            self.width, self.height = size.columns, size.lines
        except Exception:
            pass
        w, h = self.width, self.height
        lines: list[str] = []
        self.tab_regions = []
        self.card_regions = []
        self.help_box = None

        # a crew switch is "done" once the collector publishes that home
        if snap.home is not None and self.pending_home == snap.home.label:
            self.pending_home = ""
        self.columns = self.board_columns(snap.home)
        self.col_idx = max(0, min(self.col_idx, len(self.columns) - 1))
        if self.selection_auto:
            for preferred in ('captains_call', 'underway', 'charted', 'landed', 'agents'):
                if snap.cols.get(preferred) and any(key == preferred for key, _ in self.columns):
                    self.col_idx = next((i for i, (key, _) in enumerate(self.columns) if key == preferred), self.col_idx)
                    self.card_idx = 0
                    self.selection_auto = False
                    break

        # header / crew tabs
        head = f"{BOLD}{fg(C_TITLE)}Firstmate Flow{RESET} "
        x = len("Firstmate Flow ")
        active_label = self.pending_home or (snap.home.label if snap.home else "")
        for home in snap.homes:
            active = home.label == active_label
            status = snap.activity.get(home.label, "")
            if status in ("working", "blocked"):
                dot_text = "\u25cf "
                dot = f"{fg(C_OK)}\u25cf{RESET} "
            elif status:
                dot_text = "\u25cb "
                dot = f"{fg(C_DIM)}\u25cb{RESET} "
            else:
                dot_text, dot = "", ""
            dot_plain = display_width(dot_text)
            count = self.crew_tab_count(snap, home.label)
            # a known zero is a real answer ("no tickets"), so it shows (0); a
            # home never visited has no count yet and shows its bare name
            text = f"{home.label} ({count})" if count is not None else home.label
            if is_remote_home(home) and home.label in snap.offline:
                text += " \u00b7 offline"
            chip = f" {text} "
            head += dot
            if active:
                head += f"{REV}{BOLD}{fg(C_TITLE)}{chip}{RESET}"
            else:
                head += f"{fg(C_DIM)}{chip}{RESET}"
            # the whole chip (plus its trailing space) is clickable
            self.tab_regions.append((x, x + dot_plain + display_width(chip) + 1, home.label))
            head += " "
            x += dot_plain + display_width(chip) + 1
        if snap.home is not None and snap.home.label in snap.offline:
            last = snap.offline.get(snap.home.label, 0.0)
            age = _format_elapsed(time.time() - last) if last else ""
            head += (
                f"{fg(C_DIM)}\u00b7 offline, last seen {age}{RESET}"
                if age
                else f"{fg(C_DIM)}\u00b7 offline{RESET}"
            )
        # other agents stay counted on every tab; the All tab lists them
        others = snap.others
        if others:
            plural = "" if len(others) == 1 else "s"
            head += f"{fg(C_DIM)}\u00b7 {len(others)} other agent{plural}{RESET}"
            blocked = sum(1 for c in others if c.live_status == "blocked")
            if blocked:
                head += f" {fg(C_BAD)}\u26d4 {blocked} blocked{RESET}"
        lines.append(head)

        lines.append(f"{fg(C_BORDER)}{'─' * w}{RESET}")

        # column strip geometry (row-level scrolling: one card = 12 rows)
        display_columns = list(enumerate(self.columns))
        if w < len(self.columns) * 24:
            display_columns = [(self.col_idx, self.columns[self.col_idx])]
            counts = ' · '.join(f'{label} {len(snap.cols.get(key) or [])}' for key, label in
                                (('charted', 'Next'), ('underway', 'Work'), ('captains_call', 'Calls'), ('landed', 'Recent'), ('agents', 'Agents'))
                                if any(key == k for k, _ in self.columns))
            lines[1] = fg(C_DIM) + clip(counts + ' · ←/→', w) + RESET
        self.visible_column_indices = [i for i, _ in display_columns]
        gaps = len(display_columns) - 1
        colw = max(10, (w - gaps) // max(1, len(display_columns)))
        self.colw = colw
        body_top = 4          # rows 0-3: header, rule, column titles, rule
        body_bottom = h - 2   # last row is the footer/help line
        body_h = max(8, body_bottom - body_top + 1)
        self.body_h = body_h
        _debug(
            f"render w={w} h={h} body_h={body_h} scroll={dict(self.scroll)} "
            f"cards={ {k: len(v) for k, v in snap.cols.items()} }"
        )

        # clamp every column's row offset, then keep the selection visible
        for ckey, _ in self.columns:
            cards = snap.cols.get(ckey) or []
            max_off = max(0, len(cards) * CARD_ROWS - body_h)
            self.scroll[ckey] = min(max(0, self.scroll.get(ckey, 0)), max_off)
        key = self.current_key()
        cards_sel = snap.cols.get(key) or []
        if cards_sel:
            self.card_idx = min(self.card_idx, len(cards_sel) - 1)
            off = self.scroll.get(key, 0)
            top = self.card_idx * CARD_ROWS
            bottom = top + CARD_ROWS - 1
            # only move the viewport when the selected card is fully hidden;
            # a partially clipped card is still visible (smooth scrolling)
            if bottom < off:
                off = top
            elif top > off + body_h - 1:
                off = bottom - body_h + 1
            max_off = max(0, len(cards_sel) * CARD_ROWS - body_h)
            self.scroll[key] = min(max(0, off), max_off)
        else:
            self.card_idx = 0

        # column headers with scroll indicators
        header_cells = []
        for _, (ckey, title) in display_columns:
            cards = snap.cols.get(ckey) or []
            total = snap.totals.get(ckey) or len(cards)
            shown = f"{len(cards)}/{total}" if total > len(cards) else str(len(cards))
            label = f"{title} ({shown})"
            if ckey == "underway" and cards:
                # Same mapping as the web: agents Firstmate reports working now.
                label = f"{title} ({shown} · ●{activity_counts(cards).get('working', 0)} working)"
            if ckey == "landed":
                label = f"Recent Landed ({shown}) · H historial"
            if len(display_columns) == 1:
                label += f" · ←/→ {self.col_idx + 1}/{len(self.columns)}"
            blocked = sum(1 for c in cards if "blocked" in c.badge.lower())
            if blocked:
                label += f" \u26d4{blocked}"
            header_cells.append(f"{fg(C_ACCENT)}{BOLD}{pad(clip(label, colw), colw)}{RESET}")
        lines.append(f"{fg(C_BORDER)}\u2502{RESET}".join(header_cells))
        lines.append(f"{fg(C_BORDER)}{'─' * w}{RESET}")

        # cards grid: each column is a stack of 12-row card slots, scrolled by rows
        # so a wheel notch moves the content smoothly instead of by whole cards
        grid: list[list[str]] = []
        for vi, (ci, (ckey, _)) in enumerate(display_columns):
            cards = snap.cols.get(ckey) or []
            off = self.scroll.get(ckey, 0)
            x0 = vi * (colw + 1)
            col_lines = [" " * colw] * body_h
            if not cards:
                if not snap.loading:
                    placeholder = "  (empty)"
                elif snap.home is None:
                    placeholder = "  " + _presentation_text("Loading board\u2026")
                else:
                    placeholder = "  " + _presentation_text("Loading this crew\u2026")
                col_lines[0] = f"{fg(C_DIM)}{pad(placeholder, colw)}{RESET}"
            for i, card in enumerate(cards):
                top = body_top + i * CARD_ROWS - off
                if top > body_bottom or top + CARD_ROWS - 1 < body_top:
                    continue
                selected = ci == self.col_idx and i == self.card_idx
                card_lines = self.render_card(card, colw, selected, snap)
                for j, line in enumerate(card_lines):
                    y = top + j
                    if body_top <= y <= body_bottom:
                        col_lines[y - body_top] = line
                self.card_regions.append(
                    (x0, max(body_top, top), x0 + colw, min(body_bottom, top + len(card_lines) - 1) + 1, card)
                )
            grid.append(col_lines)

        for r in range(body_h):
            lines.append(f"{fg(C_BORDER)}\u2502{RESET}".join(col_lines[r] for col_lines in grid))

        # footer
        while len(lines) < h - 1:
            lines.append("")
        self.footer_hits = []
        footer_text = " " + " \u00b7 ".join(label for label, _ in _FOOTER_HINTS)
        if snap.hidden_calls and not self.reveal_answered:
            footer_text += f" \u00b7 {snap.hidden_calls} answered"
        lines.append(f"{fg(C_DIM)}{clip(footer_text, w)}{RESET}")
        if self.flash and time.time() - self.flash_at < 6:
            lines[-1] = f"{fg(C_WARN)}{clip(self.flash, w)}{RESET}"
        elif snap.error:
            lines[-1] = f"{fg(C_BAD)}{clip(snap.error, w)}{RESET}"
        else:
            x = 1
            for label, action in _FOOTER_HINTS:
                end = x + display_width(label)
                self.footer_hits.append((x, end, action))
                x = end + display_width(" \u00b7 ")

        if self.dialog is not None:
            self.render_dialog(lines, w, h)
        if self.help:
            self.render_help(lines, w, h)
        if self.message is not None:
            self.render_message(lines, w, h)
        if self.details is not None:
            # Follow the refreshed card instead of freezing an old blocker in
            # an open details view. Selection and scroll stay on this ticket.
            current = next((c for cards in snap.cols.values() for c in cards
                            if c.task == self.details.task and c.home_path == self.details.home_path), None)
            if current is not None:
                self.details = current
                self.render_details(lines, w, h)
            else:
                self.details = None
                self.say("ticket left this board; refresh to locate it")
        if self.history is not None:
            self.render_history(lines, w, h)
        if self.delivery is not None:
            self.render_delivery(lines, w, h)
        self.paint([clip_ansi(line, w) for line in lines[:h]], w, h)

    def paint(self, frame: list[str], w: int, h: int) -> None:
        """Repaint only the lines that changed.

        The board updates one ticket badge at a time without touching the rest
        of the screen, so scrolling and selection stay exactly where they are.
        """
        size = (w, h)
        if self.last_frame is None or self.last_size != size:
            # Absolute cursor positioning instead of newlines: the pane must
            # never scroll, whatever the terminal reports as its height.
            out = ["\x1b[?25l"]
            for i, line in enumerate(frame):
                out.append(f"\x1b[{i + 1};1H")
                out.append(line)
                out.append("\x1b[K")
            out.append("\x1b[J")
            self.last_frame = frame
            self.last_size = size
            sys.stdout.write("".join(out))
            sys.stdout.flush()
            return

        prev = self.last_frame
        out: list[str] = []
        for i, line in enumerate(frame):
            if i < len(prev) and prev[i] == line:
                continue
            out.append(f"\x1b[{i + 1};1H")
            out.append(line)
            out.append("\x1b[K")
        for i in range(len(frame), len(prev)):
            out.append(f"\x1b[{i + 1};1H\x1b[K")
        self.last_frame = frame
        if out:
            sys.stdout.write("\x1b[?25l" + "".join(out))
            sys.stdout.flush()

    def render_dialog(self, lines: list[str], w: int, h: int) -> None:
        """Draw the Captain's Call decision card as a centered modal.

        It mirrors the Lavish board card: the type badge and repo, the title,
        ABOUT / DECIDE context, the authored options with the recommended one
        marked, a note row, and the Queue answer action.
        """
        d = self.dialog
        if d is None:
            return
        self.dialog_hit = []
        self.dialog_box = None
        dw = max(34, min(86, w - 4))
        inner = dw - 4
        cw = inner - 4  # content width inside an option / note card
        rows: list[str] = []
        kinds: list[str] = []

        def add(
            text: str,
            kind: str,
            color: int | None = None,
            bold: bool = False,
        ) -> None:
            body = pad(text, inner)
            styled = body
            if color is not None:
                styled = fg(color) + styled
            if bold:
                styled = BOLD + styled
            if styled != body:
                styled += RESET
            rows.append(styled)
            kinds.append(kind)

        def add_segments(segments: list[tuple[str, str]], kind: str) -> None:
            """One dialog row from (text, style) segments, padded to inner."""
            used = 0
            styled = ""
            for text, style in segments:
                if not text:
                    continue
                used += display_width(text)
                styled += f"{style}{text}{RESET}" if style else text
            rows.append(styled + " " * max(0, inner - used))
            kinds.append(kind)

        def add_kv(key: str, value: str) -> None:
            first = True
            for ln in wrap_text(value, max(10, inner - 9)):
                if first:
                    add_segments([(pad(key, 8), fg(C_DIM)), (ln, "")], "ctx")
                    first = False
                else:
                    add(" " * 9 + ln, "ctx")

        def card_top(border_style: str) -> None:
            rows.append(
                f"{border_style}\u256d" + "─" * (inner - 2) + f"\u256e{RESET}"
            )

        def card_bottom(border_style: str) -> None:
            rows.append(
                f"{border_style}╰" + "─" * (inner - 2) + f"╯{RESET}"
            )

        def card_row(segments: list[tuple[str, str]], border_style: str) -> None:
            used = 0
            styled = ""
            for text, style in segments:
                if not text:
                    continue
                used += display_width(text)
                styled += f"{style}{text}{RESET}" if style else text
            styled += " " * max(0, cw - used)
            rows.append(f"{border_style}\u2502 {RESET}{styled}{border_style} \u2502{RESET}")

        badge = d.ctype.upper()
        # header: the type badge on the left, the routing repo on the right
        add_segments(
            [
                (badge, BOLD + fg(C_DECIDE if d.ctype == "decision" else C_REVIEW)),
                (" " * max(1, inner - display_width(badge) - display_width(d.repo)), ""),
                (d.repo, fg(C_DIM)),
            ],
            "head",
        )
        for ln in wrap_text(d.title, inner):
            add(ln, "title", bold=True)
        if d.about or d.decide or d.detail:
            add("", "blank")
        if d.about:
            add_kv("ABOUT", d.about)
        if d.decide:
            add_kv("DECIDE", d.decide)
        if d.detail:
            add_kv("DETAIL", d.detail)

        # every option is its own bordered card, the way the board renders it:
        # the selected one keeps the DECISION colour and a filled radio, the
        # keyboard cursor gets a marker and a brightened border, and the
        # recommendation carries the board's REC chip
        for i, opt in enumerate(d.options):
            selected = i == d.selected
            cursor = i == d.cursor and d.focus == "options" and not d.queued
            border = C_DECIDE if selected else (C_ACCENT if cursor else C_BORDER)
            border_style = fg(border) + (BOLD if selected or cursor else "")
            card_top(border_style)
            kinds.append(f"option:{i}")
            lead = "\u25b8 " if cursor else "  "
            mark = "\u25cf " if selected else "\u25cb "
            rec = opt["value"] == d.recommend
            rec_text = " REC " if rec else ""
            # A card's label is prose; show the value too whenever the two
            # differ, because the value is what gets submitted.
            value_text = "" if opt["value"] == opt["label"] else f" \u00b7 {opt['value']}"
            room = cw - display_width(lead + mark) - display_width(rec_text)
            label = clip(opt["label"], max(4, room - display_width(value_text)))
            gap = max(
                0,
                cw
                - display_width(lead + mark + label + value_text)
                - display_width(rec_text),
            )
            label_segments = [
                (lead, fg(C_ACCENT) if cursor else ""),
                (mark, fg(C_DECIDE) if selected else fg(C_DIM)),
                (label, BOLD if selected else ""),
                (value_text, fg(C_DIM)),
                (" " * gap, ""),
            ]
            if rec:
                label_segments.append((rec_text, REV + fg(C_WARN)))
            card_row(label_segments, border_style)
            kinds.append(f"option:{i}")
            if opt["hint"]:
                for ln in wrap_text(opt["hint"], max(8, cw - 2)):
                    card_row([("  " + ln, fg(C_DIM))], border_style)
                    kinds.append(f"option:{i}")
            card_bottom(border_style)
            kinds.append(f"option:{i}")
            # option cards stack tightly, the way the board lists them

        if d.freeform:
            if d.options:
                add("", "blank")
            note_border = C_ACCENT if d.focus == "note" else C_BORDER
            note_style = fg(note_border) + (BOLD if d.focus == "note" else "")
            card_top(note_style)
            kinds.append("note")
            note_text = d.note if d.note else d.freeform_hint
            note_lines = wrap_text(note_text, max(8, cw - 2))
            for li, ln in enumerate(note_lines):
                lead = "\u203a " if li == 0 else "  "
                lead_style = fg(C_ACCENT if d.note else C_DIM) if li == 0 else ""
                body_style = "" if d.note else fg(C_DIM)
                card_row([(lead, lead_style), (ln, body_style)], note_style)
                kinds.append("note")
            card_bottom(note_style)
            kinds.append("note")

        if d.error:
            add("", "blank")
            for ln in wrap_text(d.error, inner):
                add(ln, "error", C_BAD)

        body_rows = list(rows)
        body_kinds = list(kinds)
        footer_rows: list[str] = []
        footer_kinds: list[str] = []

        def add_footer(
            text: str,
            kind: str,
            color: int | None = None,
            bold: bool = False,
        ) -> None:
            body = pad(text, inner)
            styled = body
            if color is not None:
                styled = fg(color) + styled
            if bold:
                styled = BOLD + styled
            if styled != body:
                styled += RESET
            footer_rows.append(styled)
            footer_kinds.append(kind)

        def add_footer_segments(segments: list[tuple[str, str]], kind: str) -> None:
            used = 0
            styled = ""
            for text, style in segments:
                if not text:
                    continue
                used += display_width(text)
                styled += f"{style}{text}{RESET}" if style else text
            footer_rows.append(styled + " " * max(0, inner - used))
            footer_kinds.append(kind)

        add_footer("", "blank")
        status = "queueing\u2026" if d.busy else ("\u2713 queued" if d.queued else "")
        current = d.selected_option()
        reconcile = bool(current and current["value"] == "reconcile")
        if reconcile:
            button_text = " Queue reconcile request "
        elif d.close == "release":
            button_text = " Queue answer \u00b7 releases hold "
        else:
            button_text = " Queue answer "
        button_segments = [
            (button_text, REV + BOLD + fg(C_OK if d.queued else C_ACCENT))
        ]
        if status:
            button_segments.append(("  ", ""))
            button_segments.append(
                (f" {status} ", REV + fg(C_OK) if d.queued else fg(C_WARN))
            )
        add_footer_segments(button_segments, "button")
        # Consent line: what a submit does beyond the answer record itself.
        if reconcile:
            outcome = "files re-check"
            if d.intake_reconcile_wakes:
                outcome += " · queues owner wake"
            elif d.wake_home and d.wake_lane and d.wake_enabled:
                outcome += f" · steers {d.wake_lane}"
            outcome += " · binds source if unbound"
        else:
            outcome = "releases hold" if d.close == "release" else "closes task"
            if d.wake_home and d.wake_lane:
                # the intake's own wake does not depend on FM_FLOW_WAKE
                outcome += (
                    f" \u00b7 steers {d.wake_lane}"
                    if d.intake_wakes or d.wake_enabled
                    else " \u00b7 owner wake off"
                )
        add_footer(
            clip(f"\u2192 {_flatten_field(d.key)} \u00b7 {outcome}", inner),
            "hint",
            C_DIM,
        )
        help_bits = [
            "esc close",
            "\u2191\u2193 move",
            "space pick",
            "enter queue",
        ]
        # Keep Queue answer + help visible; scroll only the card body above them.
        # The whole modal must fit in the terminal (borders add two rows).
        max_inner_rows = max(6, h - 2)
        budget = max(4, max_inner_rows - len(footer_rows) - 1)
        scroll_max = max(0, len(body_rows) - budget)
        self._dialog_scroll_max = scroll_max
        if scroll_max:
            help_bits.insert(1, "pgup/pgdn or wheel scroll")
        if self._dialog_scroll_follow and d.focus == "note":
            self.dialog_scroll = scroll_max
            self._dialog_scroll_follow = False
        self.dialog_scroll = min(max(0, self.dialog_scroll), scroll_max)
        if scroll_max:
            top = self.dialog_scroll + 1
            bottom = min(len(body_rows), self.dialog_scroll + budget)
            help_bits.insert(0, f"lines {top}-{bottom} of {len(body_rows)}")
        add_footer(" \u00b7 ".join(help_bits), "help", C_DIM)

        if scroll_max:
            body_rows = body_rows[self.dialog_scroll : self.dialog_scroll + budget]
            body_kinds = body_kinds[self.dialog_scroll : self.dialog_scroll + budget]
        rows = body_rows + footer_rows
        kinds = body_kinds + footer_kinds

        box_h = len(rows) + 2
        y0 = max(0, min(max(0, h - box_h - 1), (h - box_h) // 2))
        x0 = max(0, (w - dw) // 2)
        border = fg(C_DECIDE if d.ctype == "decision" else C_BORDER)
        # the type badge lives once, on the card's own header row inside
        top = "\u256d" + "─" * (dw - 2) + "\u256e"
        box = [f"{border}{top}{RESET}"]
        for r in rows:
            box.append(f"{border}\u2502{RESET} " + r + f" {border}\u2502{RESET}")
        box.append(f"{border}╰" + "─" * (dw - 2) + f"╯{RESET}")

        self.dialog_box = (x0, y0, x0 + dw, y0 + box_h)
        left = " " * x0
        for i, dl in enumerate(box):
            y = y0 + i
            if 0 <= y < len(lines):
                lines[y] = left + dl + " " * max(0, w - x0 - ansi_width(dl))
        for i, kind in enumerate(kinds):
            y = y0 + 1 + i
            if kind.startswith("option:"):
                try:
                    idx = int(kind.split(":", 1)[1])
                except (ValueError, IndexError):
                    continue
                self.dialog_hit.append((y, x0, x0 + dw, "option", idx))
            elif kind == "button":
                self.dialog_hit.append((y, x0, x0 + dw, "button", 0))
            elif kind == "note":
                self.dialog_hit.append((y, x0, x0 + dw, "note", 0))

    HELP_ROWS = (
        ("\u2190\u2192 / h l", "move between columns"),
        ("\u2191\u2193 / j k", "move between cards"),
        ("pgup/pgdn, wheel", "scroll the column"),
        ("click a crew tab", "switch mate"),
        ("enter / click", "open a call or Landed delivery"),
        ("H", "search retained delivery history (All or local home)"),
        ("delivery", "r informe; d detalles; e explicación; s estado; Tab/Enter acciones"),
        ("o", "open the selected agent pane"),
        ("d", "read full reason, owner, dependency and next step"),
        ("m", "message Firstmate"),
        ("1-9 / tab", "switch crew (All = fleet)"),
        ("L", "show/hide Landed"),
        ("A", "show/hide the calls you already answered"),
        ("r", "refresh the board"),
        ("? / esc", "close this help"),
        ("q", "quit"),
    )

    def open_help(self) -> None:
        self.help = True
        self.help_box = None
        self.dirty = True

    def close_help(self) -> None:
        self.help = False
        self.help_box = None
        self.dirty = True

    @staticmethod
    def detail_fields(card: Card) -> list[tuple[str, str]]:
        """Evidence and routing, without turning a technical block into a call."""
        owner = getattr(card, "owner_label", "") or card.home_path or "unknown home"
        context = flow_context(card)
        fields = [("Task", card.title or card.task), ("State", card.badge),
                  ("Phase", context['label']),
                  ("Reason", context['reason']),
                  ("Native state", context['native_status']),
                  ("Dependency", dependency_caption(card) or 'No registrada'),
                  ("Waiting for", context['waiting_for'] or 'Consulta el motivo y la siguiente acción'),
                  ("Next", context['next_action']),
                  ("Firstmate action", context['firstmate_action']),
                  ("Owner", f"Firstmate · {owner}"), ("Worker", card.task)]
        report_state, report_text, report_time = durable_report(card)
        if report_state:
            fields[4:4] = [("Task report", f"{report_state}: {report_text}"),
                           ("Report time", card_time_detail(report_time))]
        event = getattr(card, "event_time", None)
        if event is not None:
            fields.append(("Time", card_time_detail(event)))
        for dep in getattr(card, 'dependencies', []):
            fields.append(('Dependency identity', f"{dep['id']} · {dep.get('title') or 'nombre no registrado'} · {dep.get('owner') or 'responsable no registrado'} · {dep.get('state') or 'estado desconocido'}"))
        blockers = getattr(card, "blockers", [])
        for b in blockers:
            fields.append(("Reason", b["text"] or "No reason recorded"))
            fields.append(("Blocker key", b["key"] or "unkeyed declaration"))
            fields.append(("Reported at", str(b["at"] or "unknown")))
        if card.bucket == "captains_call":
            next_step = "Answer the authored question in Captain's Call (Enter after closing details)."
        elif card.blocked_by not in ("", "-"):
            next_step = "Firstmate: check the named dependency and resume when it clears."
        elif any(b["state"] == "blocked" for b in blockers) or "blocked" in card.badge.lower():
            next_step = "Firstmate: take the authorized technical step or assign a named dependency owner; resume after resolution."
        elif "decision" in card.badge.lower():
            next_step = "Firstmate: reconcile the declared decision with the authoritative Captain's Call."
        elif any(s in card.badge.lower() for s in ("paused", "parked")):
            next_step = "Firstmate: check the declared wait and resume when its condition clears."
        elif getattr(card, "activity_unconfirmed", False):
            next_step = "Firstmate: verify task status; native pane activity alone does not confirm task progress."
        else:
            next_step = "Firstmate supervises this task; open its agent pane with o for live context."
        fields.append(("Routing detail", next_step))
        fields.append(("Firstmate", f"{getattr(card, 'snapshot_state', '') or 'unavailable'}; {card.doing or 'no activity detail'}"))
        fields.append(("Herdr", card.live_status or "unavailable"))
        fields.append(('Agent', UI.card_agent_line(card)))
        fields.append(('Run totals', UI.card_run_stats_suffix(card) or 'No registrados'))
        fields.append(('Branch', card.branch or 'no registrada'))
        fields.append(('Worktree', card.worktree or 'no registrado'))
        fields.append(('Supervisor evidence', delivery_text(json.dumps(getattr(card, 'supervisor', {}), ensure_ascii=False))))
        fields.append(('Planned Firstmate action', (getattr(card, 'flow', {}) or {}).get('firstmate_action') or 'No exportada'))
        fields.append(('Flow source', str((getattr(card, 'flow', {}) or {}).get('source') or 'Declaraciones retenidas; proyección no exportada')))
        fields.append(("Evidence", "Declarations are read from the latest 8192 bytes of the task log; missing dependency ownership is not inferred. Snapshot reasons may be abbreviated."))
        return fields

    def render_details(self, lines: list[str], w: int, h: int) -> None:
        card = self.details
        if card is None or w < 12 or h < 6:
            return
        dw = min(92, w - 2)
        inner = dw - 4
        rows = []
        for label, value in self.detail_fields(card):
            rows.extend(wrap_text(f"{label}: {value}", inner))
            rows.append("")
        budget = max(1, h - 6)
        self.details_max = max(0, len(rows) - budget)
        self.details_scroll = min(self.details_scroll, self.details_max)
        start = self.details_scroll
        visible = rows[start:start + budget]
        footer = f"{start + 1}-{start + len(visible)}/{len(rows)} · ↑↓/PgUp/PgDn · Esc close"
        title = clip(" Details · read only ", dw - 2)
        box = ["╭" + title + "─" * max(0, dw - 2 - display_width(title)) + "╮"]
        box += ["│ " + pad(row, inner) + " │" for row in visible]
        box += ["│ " + pad(clip(footer, inner), inner) + " │", "╰" + "─" * (dw - 2) + "╯"]
        x0, y0 = (w - dw) // 2, max(0, (h - len(box)) // 2)
        for i, row in enumerate(box):
            lines[y0 + i] = " " * x0 + row + " " * (w - x0 - dw)

    # -- message Firstmate (`m`) ---------------------------------------------
    def open_message(self) -> None:
        self.message = MessageBox()
        self.dirty = True

    def close_message(self) -> None:
        self.message = None
        self.dirty = True

    def message_target(self) -> tuple[Home | None, str]:
        snap = self.collector.snapshot()
        return supervisor_target(snap.home, snap.homes, snap.agents)

    def message_send(self) -> None:
        """Send the box text to the supervisor pane; a refusal keeps the text."""
        m = self.message
        if m is None:
            return
        self.dirty = True
        text = _flatten_field(m.text)
        if not text:
            m.error = "the message is empty"
            return
        if len(text) > MESSAGE_LIMIT:
            m.error = f"the message is too long ({MESSAGE_LIMIT} characters maximum)"
            return
        home, pane = self.message_target()
        if not pane:
            m.error = f"no Firstmate pane found for {home.label if home else 'this tab'}"
            return
        ok, detail = send_to_firstmate(pane, text)
        if not ok:
            m.error = detail or "herdr refused the message"
            return
        self.message = None
        self.say(f"sent to firstmate ({pane})")

    def render_message(self, lines: list[str], w: int, h: int) -> None:
        """Draw the `m` box as a centered modal: its target, the text, the keys."""
        m = self.message
        if m is None:
            return
        home, pane = self.message_target()
        dw = max(34, min(80, w - 4))
        inner = dw - 4
        where = home.label if home else "this tab"
        target = f"to {where} ({pane})" if pane else f"to {where}: no Firstmate pane found"
        rows: list[tuple[str, int | None]] = [(target, C_DIM)]
        rows += [(ln, None) for ln in wrap_text("> " + m.text + "\u258f", inner)[-6:]]
        if m.error:
            rows += [(ln, C_BAD) for ln in wrap_text(m.error, inner)]
        rows.append(("enter send \u00b7 esc close \u00b7 ctrl+u clear", C_DIM))
        border = fg(C_BORDER)
        title = " Message Firstmate "
        box = [
            f"{border}\u256d\u2500{title}"
            + "\u2500" * max(0, dw - 3 - display_width(title))
            + f"\u256e{RESET}"
        ]
        for text, color in rows:
            body = pad(text, inner)
            if color is not None:
                body = f"{fg(color)}{body}{RESET}"
            box.append(f"{border}\u2502{RESET} {body} {border}\u2502{RESET}")
        box.append(f"{border}\u2570" + "\u2500" * (dw - 2) + f"\u256f{RESET}")
        y0 = max(0, (h - len(box)) // 2)
        x0 = max(0, (w - dw) // 2)
        left = " " * x0
        for i, dl in enumerate(box):
            y = y0 + i
            if 0 <= y < len(lines):
                lines[y] = left + dl + " " * max(0, w - x0 - ansi_width(dl))

    def render_help(self, lines: list[str], w: int, h: int) -> None:
        """Draw the keybinding help as a centered modal (footer ``? help``)."""
        self.help_box = None
        keyw = max(display_width(key) for key, _ in self.HELP_ROWS)
        cw = keyw + 3 + max(display_width(desc) for _, desc in self.HELP_ROWS)
        dw = min(max(34, cw + 4), max(34, w - 4))
        inner = dw - 4
        box_h = len(self.HELP_ROWS) + 2
        y0 = max(0, min(max(0, h - box_h - 1), (h - box_h) // 2))
        x0 = max(0, (w - dw) // 2)
        border = fg(C_BORDER)
        box = [f"{border}\u256d─ Help " + "─" * max(0, dw - 9) + f"\u256e{RESET}"]
        for key, desc in self.HELP_ROWS:
            key_text = pad(key, keyw + 3)
            desc_text = clip(desc, max(0, inner - keyw - 3))
            styled = f"{fg(C_DIM)}{key_text}{RESET}{desc_text}"
            padding = " " * max(
                0, inner - display_width(key_text) - display_width(desc_text)
            )
            box.append(f"{border}\u2502{RESET} {styled}{padding} {border}\u2502{RESET}")
        box.append(f"{border}╰" + "─" * (dw - 2) + f"╯{RESET}")
        self.help_box = (x0, y0, x0 + dw, y0 + box_h)
        left = " " * x0
        for i, dl in enumerate(box):
            y = y0 + i
            if 0 <= y < len(lines):
                lines[y] = left + dl + " " * max(0, w - x0 - ansi_width(dl))

    def _crew_label(self, snap: Snapshot, home_path: str) -> str:
        if not home_path:
            return ""
        target = os.path.realpath(home_path)
        for home in snap.homes:
            if home.path and os.path.realpath(home.path) == target:
                return home.label
        return os.path.basename(home_path) or home_path

    def render_card(self, card: Card, colw: int, selected: bool, snap: Snapshot | None = None) -> list[str]:
        border_c = C_TITLE if selected else C_BORDER
        cardw = max(10, colw - 1)  # one column of breathing room between cards
        inner = max(4, cardw - 4)
        top_lbl = clip(self.card_agent_line(card) if card.bucket == 'landed' else card.id, max(1, inner - 1))
        left = f"\u256d─ {top_lbl} "
        right_plain = "\u256e"
        dash_n = max(0, cardw - display_width(left) - display_width(right_plain))
        top = f"{fg(border_c)}{left}{'─' * dash_n}{right_plain}{RESET}"
        top += " " * max(0, cardw - display_width(top))

        def mid(plain: str, color: int | None = None) -> str:
            body = pad(plain, inner)
            if color is not None and body.strip():
                body = f"{fg(color)}{body}{RESET}"
            return f"\u2502 {body} \u2502"

        def excerpt(text: str, count: int) -> list[str]:
            rows = wrap_text(delivery_text(text), inner)
            if len(rows) > count:
                rows = rows[:count - 1] + [clip(' '.join(rows[count - 1:]), inner)]
            return (rows + [''] * count)[:count]

        event = getattr(card, 'event_time', None)
        when = compact_time_caption(event, inner) if event is not None else ''
        if card.bucket == 'landed':
            summary = getattr(card, 'delivery_summary', '') or getattr(card, 'delivery_problem', '') or (card.title + ' · Sin resumen de resultado; Enter abre las fuentes.')
            body = [mid(row) for row in excerpt(summary, 7)]
            body += [mid(row, C_DIM) for row in excerpt(when, 2)]
            body += [mid('Enter entrega · H historial', C_ACCENT)]
        elif card.bucket == OTHER_AGENTS_COLUMN[0]:
            body = [mid(card.badge, card.badge_color), mid(self.card_agent_line(card), C_DIM)]
            body += [mid(row) for row in excerpt(card.title or card.task, 3)]
            body += [mid(card.doing, C_DIM), mid(card.mode, C_DIM)]
            body += [mid(''), mid(''), mid('Enter / o: abrir agente', C_ACCENT)]
        else:
            context = flow_context(card)
            report_state, _report_text, report_time = durable_report(card)
            if report_state and getattr(card, 'activity_unconfirmed', False):
                when = compact_time_caption(report_time, inner)
            title = card.title or card.task
            body = [mid(row) for row in excerpt(title, 2)]
            body += [mid(context['label'], card.badge_color)]
            body += [mid(row, C_WARN) for row in excerpt(context['reason'], 2)]
            dependency = dependency_caption(card)
            relation = ('Depende de: ' + dependency) if dependency else context['waiting_for'] or context['next_action']
            relation = 'Firstmate · ' + (getattr(card, 'owner_label', '') or 'responsable no registrado') + ': ' + relation
            body += [mid(row) for row in excerpt(relation, 2)]
            # The workflow report and native observation are independent.
            body += [mid(clip(context['native_status'], inner), C_DIM)]
            body += [mid(clip(when, inner), C_DIM)]
            body += [mid('Enter: texto completo · o agente', C_ACCENT)]
        lines = [f'{fg(border_c)}{top}{RESET}', *body,
                 f"{fg(border_c)}{'╰' + '─' * (cardw - 2) + '╯'}{RESET}"]
        # pad each row to the full column width so the separator keeps a gap
        return [line + " " * max(0, colw - cardw) for line in lines]

    @staticmethod
    def card_agent_line(card: Card) -> str:
        """``account·model·effort``; the account names the subscription store.

        A pi worker's model carries its provider prefix (``deepseek/...``), so
        the prefix is dropped when the account line already shows it.
        """
        if getattr(card, "bucket", "") == "landed":
            identity = getattr(card, 'delivery_identity', '')
            if not identity:
                return 'owner unavailable · Delivery'
            return ("read" if delivery_seen(identity, card.task) else "new") + " · Delivery"
        if getattr(card, "bucket", "") == "captains_call":
            return "Human action"
        lead = getattr(card, "account", "") or card.agent
        model = card.model
        if lead and model.startswith(lead + "/"):
            model = model.split("/", 1)[1]
        parts = [p for p in (lead, model, card.effort) if p]
        return "\u00b7".join(parts) if parts else "no agent yet"

    @staticmethod
    def card_run_stats_suffix(card: Card) -> str:
        """Herdr-style ``9m 59s · ↓ 55.5k tokens`` suffix for the status line."""
        elapsed = getattr(card, "run_elapsed", "") or ""
        tokens = getattr(card, "run_tokens", "") or ""
        if not elapsed and not tokens:
            return ""
        parts: list[str] = []
        if elapsed:
            parts.append(elapsed)
        if tokens:
            parts.append(f"\u2193 {tokens} tokens")
        return " \u00b7 ".join(parts)

    @staticmethod
    def card_run_stats_line(card: Card) -> str:
        """Legacy helper; prefer ``card_run_stats_suffix`` on the status row."""
        suffix = UI.card_run_stats_suffix(card)
        return suffix.strip(" ()") if suffix else ""

    @staticmethod
    def card_status_line(card: Card, inner: int) -> str:
        """Run totals on the status row; the doing text only when it adds detail.

        A ``doing`` that just repeats the badge (``validating`` or
        ``validating: ...``) or the generic ``harness busy ...`` liveness line is
        dropped, so the row carries only the totals. Validating and blocked rows
        also carry the thinking effort when the row has room for it.
        """
        blockers = getattr(card, "blockers", [])
        if blockers and card.bucket != "captains_call":
            blocker = next((b for b in blockers if b["state"] == "blocked"), blockers[0])
            return clip("Reason: " + (blocker["text"] or "No reason recorded"), inner)
        base = task_report_text(card.doing or "")
        if getattr(card, "bucket", "") == "captains_call":
            return clip(base, inner)
        unconfirmed = (
            getattr(card, "bucket", "") in ("underway", "awaiting_merge")
            and getattr(card, "activity_unconfirmed", False)
        )
        if unconfirmed:
            # A task declaration remains useful while runtime confirmation is
            # unavailable. Label it so an old working line cannot become live
            # evidence. Raw runtime diagnostics remain available in --once.
            reported = getattr(card, "report_text", task_report_text(getattr(card, "status_text", "")))
            base = f"Report: {reported}" if reported else "No task report"
        elif not base:
            reported = getattr(card, "report_text", task_report_text(getattr(card, "status_text", "")))
            base = f"Report: {reported}" if reported else ""
        label = badge_label_from_badge(getattr(card, "badge", "") or "")
        low = base.lower()
        if low.startswith("harness busy") or (label and low.startswith(label)):
            base = ""
        stats = UI.card_run_stats_suffix(card)
        if not base and not stats:
            return ""
        plain = f"{base} \u00b7 {stats}" if base and stats else (base or stats)
        if label in ("validating", "blocked"):
            effort = (getattr(card, "effort", "") or "").strip()
            if effort:
                with_effort = f"{plain} \u00b7 {effort}" if plain else effort
                if not inner or display_width(with_effort) <= inner:
                    plain = with_effort
        return clip(plain, inner)

    @staticmethod
    def card_footer_line(card: Card, crew: str = "") -> str:
        crew_bit = f"\u2316 {crew} \u00b7 " if crew else ""
        if card.bucket == "landed":
            return "Enter delivery · H history"
        if getattr(card, "blockers", []) or "blocked" in card.badge.lower() or getattr(card, "blocked_by", "") not in ("", "-"):
            return f"d details · {crew or getattr(card, 'owner_label', '') or card.task}"
        if card.worktree:
            parts = card.worktree.rstrip("/").split("/")
            short = "/".join(parts[-2:])
            return f"{crew_bit}\u2338 {short}"
        if card.bucket == "charted":
            return f"{crew_bit}no worktree yet" if crew else "no worktree yet"
        if crew:
            return crew_bit.rstrip(" \u00b7 ")
        return card.mode or card.kind or ""


def clip_ansi(line: str, width: int) -> str:
    """Clip a line to width terminal cells, keeping ANSI sequences intact."""
    out = []
    used = 0
    i = 0
    while i < len(line) and used < width:
        ch = line[i]
        if ch == "\x1b":
            m = re.match(r"\x1b\[[0-9;?]*[a-zA-Z]", line[i:])
            if m:
                out.append(m.group(0))
                i += len(m.group(0))
                continue
        w = display_width(ch)
        if used + w > width:
            break
        out.append(ch)
        used += w
        i += 1
    return "".join(out) + RESET


# ----------------------------------------------------------------------------
# input
# ----------------------------------------------------------------------------


def parse_input(buf: bytes, ui: UI) -> bytes:
    """Parse a chunk of stdin bytes; returns unconsumed remainder."""
    while buf:
        if buf.startswith(b"\x1b[<"):
            m = re.match(rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])", buf)
            if not m:
                if len(buf) < 24 and b"M" not in buf and b"m" not in buf:
                    return buf
                buf = buf[1:]
                continue
            but, x, y, kind = _int(m.group(1)), _int(m.group(2)), _int(m.group(3)), m.group(4)
            buf = buf[m.end() :]
            if kind == b"M":
                ui.click(x - 1, y - 1, but)
                ui.dirty = True
            continue
        if buf.startswith(b"\x1b["):
            km = re.match(rb"\x1b\[(\d+);(\d+)u", buf)
            if km:
                cp, mod = _int(km.group(1)), _int(km.group(2))
                buf = buf[km.end() :]
                ui.dirty = True
                if ui.delivery is not None or ui.history is not None:
                    if ui.delivery is not None:
                        buf = chr(cp).encode('utf-8') + buf
                    elif ui.history is not None and chr(cp).isprintable():
                        ui.history["query"] += chr(cp)
                        ui.history["index"] = 0
                elif ui.message is not None:
                    if chr(cp).isprintable():
                        ui.message.text += chr(cp)
                elif ui.details is not None:
                    pass
                # Kitty keyboard protocol: shift+L toggles Landed (lowercase l is column right)
                elif cp in (76, 108) and (mod & 1):
                    ui.toggle_landed()
                continue
            m = re.match(rb"\x1b\[([0-9]*)([ABCDZHF~])", buf)
            if not m:
                if len(buf) < 8:
                    return buf
                buf = buf[1:]
                continue
            num, code = m.group(1), m.group(2)
            buf = buf[m.end() :]
            ui.dirty = True
            if ui.delivery is not None or ui.history is not None:
                delta = -1 if code == b"A" else 1 if code == b"B" else 0
                if code == b"~" and num in (b"5", b"6"):
                    page = ui.delivery_page_rows if ui.delivery is not None else max(1, ui.height - 5)
                    delta = (-1 if num == b"5" else 1) * page
                if ui.delivery is not None:
                    if code == b'Z':
                        ui.delivery_action_focus = (ui.delivery_action_focus - 1) % len(ui.delivery_actions)
                        continue
                    if code == b"H":
                        delta = -ui.delivery_max
                    elif code == b"F":
                        delta = ui.delivery_max
                    ui.delivery_scroll_by(delta)
                else:
                    if code == b"H":
                        delta = -len(ui.history_rows())
                    elif code == b"F":
                        delta = len(ui.history_rows())
                    ui.history_move(delta)
                continue
            if ui.message is not None:
                continue  # arrows and paging do nothing in the `m` box
            if ui.details is not None:
                if code in (b"A", b"B"):
                    ui.details_scroll_by(-1 if code == b"A" else 1)
                elif code == b"~" and num in (b"5", b"6"):
                    ui.details_scroll_by((-1 if num == b"5" else 1) * max(1, ui.height - 8))
                elif code == b"H":
                    ui.details_scroll_by(-ui.details_max)
                elif code == b"F":
                    ui.details_scroll_by(ui.details_max)
                continue
            if ui.dialog is not None:
                if code == b"A":
                    ui.dialog_move(-1)
                elif code == b"B":
                    ui.dialog_move(1)
                elif code == b"Z":
                    ui.dialog_toggle_focus()
                elif code == b"~" and num == b"5":
                    ui.dialog_scroll_by(-max(1, ui.body_h // 3))
                elif code == b"~" and num == b"6":
                    ui.dialog_scroll_by(max(1, ui.body_h // 3))
                continue
            if code == b"A":
                ui.card_idx = max(0, ui.card_idx - 1)
            elif code == b"B":
                ui.card_idx += 1
            elif code == b"C":
                ui.selection_auto = False
                ui.col_idx = min(len(ui.columns) - 1, ui.col_idx + 1)
                ui.card_idx = 0
            elif code == b"D":
                ui.selection_auto = False
                ui.col_idx = max(0, ui.col_idx - 1)
                ui.card_idx = 0
            elif code == b"Z":
                ui.next_home(-1)
            elif code == b"~" and num == b"5":
                ui.scroll_selected(-ui.body_h)
            elif code == b"~" and num == b"6":
                ui.scroll_selected(ui.body_h)
            elif code == b"H" or (code == b"~" and num == b"1"):
                ui.scroll_bound(bottom=False)
            elif code == b"F" or (code == b"~" and num == b"4"):
                ui.scroll_bound(bottom=True)
            continue
        if buf[0] == 0x1b:
            buf = buf[1:]
            if ui.delivery is not None:
                ui.delivery = None
                ui.dirty = True
            elif ui.history is not None:
                ui.history = None
                ui.dirty = True
            elif ui.message is not None:
                ui.close_message()
            elif ui.details is not None:
                ui.details = None
                ui.dirty = True
            elif ui.dialog is not None:
                ui.close_dialog()
            elif ui.help:
                ui.close_help()
            continue
        # decode one whole character, waiting for the rest of a multibyte
        # sequence instead of dropping it (captain notes are typed here)
        lead = buf[0]
        size = 1
        if lead & 0xE0 == 0xC0:
            size = 2
        elif lead & 0xF0 == 0xE0:
            size = 3
        elif lead & 0xF8 == 0xF0:
            size = 4
        if len(buf) < size:
            return buf
        chunk = buf[:size]
        buf = buf[size:]
        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError:
            continue
        ui.dirty = True
        if ui.delivery is not None:
            if text in ("j", "k"):
                ui.delivery_scroll_by(1 if text == "j" else -1)
            elif text == "o" and ui.delivery_card is not None:
                ui.open_card_pane(ui.delivery_card)
            elif text == 'r':
                ui.delivery_report()
            elif text == 'd':
                ui.delivery_set_view('details')
            elif text == 'e':
                ui.delivery_explain()
            elif text == 's':
                ui.delivery_explain(status=True)
            elif text == '\t':
                ui.delivery_action_focus = (ui.delivery_action_focus + 1) % len(ui.delivery_actions)
            elif text in ('\r', '\n'):
                ui.delivery_action(ui.delivery_actions[ui.delivery_action_focus])
            elif text == "q":
                ui.delivery = None
            elif text in ("\x03", "\x04"):
                ui.quitting = True
            continue
        if ui.history is not None:
            if text == "\t":
                ui.history_info()
            elif text in ("\r", "\n"):
                ui.history_open()
            elif text in ("\x7f", "\b"):
                ui.history["query"] = ui.history["query"][:-1]
                ui.history["index"] = 0
            elif text == "\x15":
                ui.history["query"] = ""
                ui.history["index"] = 0
            elif text in ("\x03", "\x04"):
                ui.quitting = True
            elif text.isprintable():
                ui.history["query"] += text
                ui.history["index"] = 0
            continue
        if ui.message is not None:
            # the `m` box owns the keyboard: Enter sends, every printable key
            # types, and a pasted line feed becomes a space
            if text in ("\x03", "\x04"):
                ui.quitting = True
            elif text == "\r":
                ui.message_send()
            elif text in ("\x7f", "\b"):
                ui.message.text = ui.message.text[:-1]
            elif text == "\x15":  # ctrl+u clears the text
                ui.message.text = ""
            elif text == "\n":
                ui.message.text += " "
            elif text.isprintable():
                ui.message.text += text
            continue
        if ui.details is not None:
            if text in ("q", "d"):
                ui.details = None
            elif text in ("j", "k"):
                ui.details_scroll_by(1 if text == "j" else -1)
            elif text in ("\x03", "\x04"):
                ui.quitting = True
            continue
        if text == "\x0c":  # ctrl+L toggles Landed when the terminal sends it
            ui.toggle_landed()
            continue
        if ui.dialog is not None:
            # the modal owns the keyboard: Esc closes, arrows/space choose,
            # every printable key edits the note
            if text in ("\x03", "\x04"):
                ui.quitting = True
            elif text in ("\r", "\n"):
                ui.dialog_submit()
            elif text in ("\x7f", "\b"):
                ui.dialog_backspace()
            elif text == "\x15":  # ctrl+u clears the note
                ui.dialog.note = ""
                ui._dialog_scroll_follow = True
                ui.dirty = True
            elif text == "\t":
                ui.dialog_toggle_focus()
            elif text == " ":
                if ui.dialog.focus == "note":
                    ui.dialog_type(" ")
                else:
                    ui.dialog_pick()
            elif text.isprintable():
                ui.dialog_type(text)
            continue
        if ui.help:
            if text in ("?", "h", "q", " "):
                ui.close_help()
            continue
        if text in ("q", "\x03", "\x04"):
            ui.quitting = True
        elif text == "r":
            ui.collector.refresh_now()
            ui.say("refreshing\u2026")
        elif text == "\t":
            ui.next_home(1)
        elif text in "123456789":
            snap = ui.collector.snapshot()
            i = _int(text) - 1
            if i < len(snap.homes):
                ui.switch_home(snap.homes[i].label)
        elif text in ("[",):
            ui.next_home(-1)
        elif text in ("]",):
            ui.next_home(1)
        elif text in ("g",):
            ui.scroll_bound(bottom=False)
        elif text in ("G",):
            ui.scroll_bound(bottom=True)
        elif text in ("j",):
            ui.card_idx += 1
        elif text in ("k",):
            ui.card_idx = max(0, ui.card_idx - 1)
        elif text in ("l",):
            ui.selection_auto = False
            ui.col_idx = min(len(ui.columns) - 1, ui.col_idx + 1)
            ui.card_idx = 0
        elif text in ("h",):
            ui.selection_auto = False
            ui.col_idx = max(0, ui.col_idx - 1)
            ui.card_idx = 0
        elif text == "L":
            ui.toggle_landed()
        elif text == "A":
            ui.toggle_answered()
        elif text == "o":
            ui.open_selected()
        elif text == "H":
            ui.open_history()
        elif text == "d":
            ui.open_details()
        elif text == "m":
            ui.open_message()
        elif text == "?":
            ui.open_help()
        elif text in ("\r", "\n"):
            ui.activate()
    return buf


# ----------------------------------------------------------------------------
# non-interactive mode
# ----------------------------------------------------------------------------


def _print_home_cols(label: str, path: str, cols: dict[str, list[Card]]) -> None:
    print(f"== {label}  ({path})")
    for key, title in COLUMNS:
        cards = cols.get(key) or []
        print(f"  {title} ({len(cards)})")
        for c in cards:
            agent = UI.card_agent_line(c)
            pane = c.pane_id or "-"
            line = f"    {c.id} [{c.badge}] {agent} pane={pane}"
            if c.bucket == "charted":
                line += f" wt={c.worktree or '-'}"
            print(line)
            event = getattr(c, "event_time", None)
            if event is not None:
                print(f"      Time: {card_time_line(event, 80)}; {card_time_detail(event)}")
            if c.bucket in ("underway", "awaiting_merge"):
                print(f"      Firstmate: {c.snapshot_state or 'unavailable'}; {c.doing or 'no activity detail'}")
                print(f"      Herdr: {c.live_status or 'unavailable'}")
                if c.status_state or c.status_text:
                    print(f"      Reported: {c.status_state or '-'}; {c.status_text}")
                if getattr(c, "report_text", ""):
                    print(f"      Task report [at={c.report_at or 'unknown'}]: {c.report_text}")
            if getattr(c, "blockers", []) or c.blocked_by not in ("", "-"):
                for label, value in UI.detail_fields(c):
                    print(f"      {label}: {value}")
    print()


def _print_other_agents(cards: list[Card]) -> None:
    print(f"== Other agents ({len(cards)})")
    for c in cards:
        print(f"    {c.pane_id} [{c.badge}] {c.agent or '-'} {c.id}")
    print()


def once(active_label: str = "", all_homes: bool = False) -> int:
    homes = discover_homes()
    if not homes:
        print("no Firstmate homes found (set FM_FLOW_HOMES or homes.conf)", file=sys.stderr)
        return 1
    state = herdr_state()
    agents, panes = state["agents"], state["panes"]
    meta_index = build_meta_index(homes)
    if active_label == ALL_CREW_LABEL:
        all_homes = True
        active_label = ""
    # The synthetic All home has no path to snapshot, so the fleet view is the
    # merge of the real homes - the same one the interactive board shows.
    fleet = real_homes(homes)
    if active_label:
        selected = [h for h in fleet if h.label == active_label] or fleet
    else:
        selected = fleet
    parts: list[tuple[Home, dict[str, list[Card]], dict[str, int]]] = []
    bearings = {delivery_home_identity(home): (bearings_snapshot(home), time.time()) for home in selected}
    for home in selected:
        snap = bearings[delivery_home_identity(home)][0]
        cols: dict[str, list[Card]] = {k: [] for k, _ in COLUMNS}
        totals: dict[str, int] = {k: 0 for k, _ in COLUMNS}
        if snap:
            cols, totals = make_cards(home, snap, meta_index, agents, panes, homes, bearings=bearings)
        parts.append((home, cols, totals))
    if all_homes:
        merged, _totals = merge_fleet_columns(parts)
        _print_home_cols(ALL_CREW_LABEL, f"{len(parts)} homes", merged)
    for home, cols, _totals in parts:
        where = f"{home.route}: {home.path}" if is_remote_home(home) else home.path
        _print_home_cols(home.label, where, cols)
    _print_other_agents(
        other_agent_cards(state, fleet, firstmate_pane_ids(meta_index), {}, time.time())
    )
    return 0


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if "--homes" in argv:
        discover_homes(verbose=True)
        return 0
    if os.name == 'nt':
        print('TUI collection requires a POSIX backend; use the web UI or run with Firstmate in WSL.', file=sys.stderr)
        return 2
    if "--once" in argv:
        active = ""
        if "--home" in argv:
            active = argv[argv.index("--home") + 1]
        return once(active_label=active, all_homes="--all" in argv)

    if not os.isatty(0) or not os.isatty(1):
        return once()

    ui = UI()
    ui.collector.start()

    fd = sys.stdin.fileno()
    old = None
    try:
        import termios
        import tty

        old = termios.tcgetattr(fd)
        tty.setraw(fd)
        sys.stdout.write("\x1b[?1049h\x1b[?1000h\x1b[?1006h\x1b[?25l")
        sys.stdout.flush()

        pending = b""
        while not ui.quitting:
            snap = ui.collector.snapshot()
            now = time.time()
            if (
                ui.dialog is not None
                and ui.dialog.queued
                and now - ui.dialog.done_at > 1.6
            ):
                # the answer landed: retire the modal and re-read the fleet so
                # the answered ticket leaves Captain's Call
                ui.dialog = None
                ui.collector.refresh_now()
                ui.dirty = True
            # repaint immediately on input; the diff painter makes this cheap
            if ui.dirty or snap.seq != ui.last_seq or now - ui.last_render > 1.0:
                ui.last_seq = snap.seq
                ui.last_render = now
                ui.dirty = False
                ui.render(snap)
            r, _, _ = select.select([fd], [], [], 0.25)
            if r:
                data = os.read(fd, 4096)
                if not data:
                    break
                pending = parse_input(pending + data, ui)
                # coalesce a burst of wheel/key events into a single repaint
                for _ in range(16):
                    r2, _, _ = select.select([fd], [], [], 0.0)
                    if not r2:
                        break
                    more = os.read(fd, 4096)
                    if not more:
                        break
                    pending = parse_input(pending + more, ui)
    finally:
        try:
            sys.stdout.write("\x1b[?1006l\x1b[?1000l\x1b[?25h\x1b[?1049l")
            sys.stdout.flush()
        except Exception:
            pass
        if old is not None:
            import termios

            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        ui.collector.stop()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(0)
