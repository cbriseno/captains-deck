#!/usr/bin/env python3
"""Open work past Firstmate's 20-row bounds still reaches the board.

The producer stub below keeps the bounds of fm-bearings-snapshot.sh: in-flight
and queued rows stop at 20 unless --all-in-flight / --all-queued is passed.
A legacy stub rejects those flags with exit 2, as an older Firstmate does. The
real-producer test runs the Firstmate checkout's own fm-bearings-snapshot.sh on
a disposable home with more than 20 open rows, and skips when no checkout exists.

Run: python3 tests/test_flow_bearings_bounds.py   (or via pytest)
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "flow_tui", ROOT / "scripts" / "flow_tui.py"
)
assert _spec is not None and _spec.loader is not None
flow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(flow)

OPEN_IN_FLIGHT = 22
OPEN_QUEUED = 25

# Stands in for bin/fm-bearings-snapshot.sh: same caps, same flag names.
PRODUCER_STUB = """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = set(sys.argv[1:])
payload = json.loads((Path(__file__).with_name("payload.json")).read_text())
caps = {"in_flight": 20, "gates": 20}
if "--all-in-flight" in args:
    caps["in_flight"] = None
if "--all-queued" in args:
    caps["gates"] = None
out = dict(payload)
for key, cap in caps.items():
    rows = payload[key]
    out[key] = rows if cap is None else rows[:cap]
    if cap is not None and len(rows) > cap:
        out.setdefault("omitted", []).append({"surface": f"{key} showing {cap} of {len(rows)}"})
print(json.dumps(out))
"""


LEGACY_STUB = """#!/usr/bin/env python3
import sys

if {"--all-in-flight", "--all-queued"} & set(sys.argv[1:]):
    print("usage: fm-bearings-snapshot.sh", file=sys.stderr)
    raise SystemExit(2)
PRODUCER_BODY
"""

_REAL_ENV = os.environ.get("FM_REAL_PRODUCER_ROOT")
REAL_ROOT = Path(_REAL_ENV) if _REAL_ENV else None


def _payload() -> dict:
    # Mixed states: working and validating underway rows; gates with a plain
    # reason and a blocked-by reason, so both kinds reach the Up next column.
    in_flight = [
        {
            "id": f"demo/work-{i}",
            "name": f"Underway task {i}",
            "doing": "harness busy (claude-hook)" if i % 2 else "checking the diff",
            "state": "working" if i % 2 else "validating",
            "kind": "ship",
            "repo": "demo",
        }
        for i in range(OPEN_IN_FLIGHT)
    ]
    gates = [
        {
            "id": f"demo-queued-{i}",
            "title": f"Queued gate {i}",
            "reason": "-" if i % 3 else "waiting for install",
            "blocked_by": "-" if i % 3 else f"demo-queued-{i - 1}",
            "owner": "(main)",
            "filed": f"2026-10-{(i % 28) + 1:02d}",
        }
        for i in range(OPEN_QUEUED)
    ]
    return {
        "schema": "fm-bearings.v1",
        "home": "demo",
        "generated": "2026-10-30T14:00:00Z",
        "gates": gates,
        "in_flight": in_flight,
        "decisions_open": [],
        "landed": [],
        "paths": [],
        "endpoints": [],
        "secondmates": [],
        "recorded_prs": [],
    }


def _home(root: Path) -> "flow.Home":
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    stub = bin_dir / "fm-bearings-snapshot.sh"
    stub.write_text(PRODUCER_STUB)
    stub.chmod(0o755)
    (bin_dir / "payload.json").write_text(json.dumps(_payload()))
    return flow.Home("demo", str(root), "captain")


def test_default_flags_lift_in_flight_and_queued_bounds() -> None:
    saved = os.environ.pop("FM_FLOW_ALL", None)
    try:
        flags = flow.bearings_flags()
    finally:
        if saved is not None:
            os.environ["FM_FLOW_ALL"] = saved
    assert "--all-in-flight" in flags, flags
    assert "--all-queued" in flags, flags
    # Decisions and landed keep Firstmate's own bounds and hold buckets.
    assert "--all-decisions" not in flags, flags
    assert "--all-landed" not in flags, flags


def test_fm_flow_all_also_lifts_landed() -> None:
    saved = os.environ.get("FM_FLOW_ALL")
    os.environ["FM_FLOW_ALL"] = "1"
    try:
        flags = flow.bearings_flags()
    finally:
        if saved is None:
            os.environ.pop("FM_FLOW_ALL", None)
        else:
            os.environ["FM_FLOW_ALL"] = saved
    assert {"--all-in-flight", "--all-queued", "--all-landed"} <= set(flags), flags


def test_open_work_above_twenty_reaches_the_board() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(Path(tmp, "home"))
        snap = flow.bearings_snapshot(home)
        assert len(snap["gates"]) == OPEN_QUEUED, len(snap["gates"])
        assert len(snap["in_flight"]) == OPEN_IN_FLIGHT, len(snap["in_flight"])
        cols, totals = flow.make_cards(home, snap, {}, {}, {}, [home])
        assert totals["charted"] == OPEN_QUEUED, totals
        assert len(cols["charted"]) == OPEN_QUEUED, len(cols["charted"])
        assert len(cols["underway"]) + len(cols["captains_call"]) == OPEN_IN_FLIGHT


def _legacy_home(root: Path) -> "flow.Home":
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    body = PRODUCER_STUB.split("\n", 1)[1]
    stub = bin_dir / "fm-bearings-snapshot.sh"
    stub.write_text(LEGACY_STUB.replace("PRODUCER_BODY", body))
    stub.chmod(0o755)
    (bin_dir / "payload.json").write_text(json.dumps(_payload()))
    return flow.Home("demo", str(root), "captain")


def test_legacy_producer_keeps_home_with_its_own_bound() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = _legacy_home(Path(tmp, "home"))
        snap = flow.bearings_snapshot(home)
        assert snap, "an older Firstmate must not drop the home"
        assert snap.get("open_work_capped") is True, snap.keys()
        assert len(snap["gates"]) == 20, len(snap["gates"])
        assert len(snap["in_flight"]) == 20, len(snap["in_flight"])


def test_real_producer_shows_every_open_row() -> None:
    if REAL_ROOT is None or not (REAL_ROOT / "bin" / "fm-bearings-snapshot.sh").is_file():
        print("skip: set FM_REAL_PRODUCER_ROOT to a Firstmate checkout to run")
        return
    queued = 25
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp, "home")
        (root / "data").mkdir(parents=True)
        (root / "state").mkdir()
        lines = ["# Backlog", "", "## In flight", "", "## Queued"]
        lines += [f"- [ ] real-queued-{i:02d} - Queued gate {i} (repo: demo) (kind: ship) "
                  f"(priority: 1) (since 2026-10-29)" for i in range(1, queued + 1)]
        lines += ["", "## Done", ""]
        (root / "data" / "backlog.md").write_text("\n".join(lines))
        home = flow.Home("demo", str(root), "captain")
        saved = os.environ.get("FM_ROOT_OVERRIDE")
        os.environ["FM_ROOT_OVERRIDE"] = str(REAL_ROOT)
        try:
            snap = flow.bearings_snapshot(home)
            assert snap and not snap.get("open_work_capped"), snap.get("open_work_capped")
            cols, totals = flow.make_cards(home, snap, {}, {}, {}, [home])
            shown = {c.task for c in cols["charted"]}
            for i in range(1, queued + 1):
                assert f"real-queued-{i:02d}" in shown, i
            assert totals["charted"] == len(shown) >= queued, (totals, len(shown))
            # The same home through the query Firstmate had before the lift.
            with mock.patch.object(flow, "bearings_flags", return_value=[
                    "--json", "--fields", "paths,endpoints"]):
                old = flow.bearings_snapshot(home)
            old_cols, _ = flow.make_cards(home, old, {}, {}, {}, [home])
            assert len(old_cols["charted"]) == 20, len(old_cols["charted"])
        finally:
            if saved is None:
                os.environ.pop("FM_ROOT_OVERRIDE", None)
            else:
                os.environ["FM_ROOT_OVERRIDE"] = saved


def test_counterfactual_old_flags_drop_open_work() -> None:
    # The first cut's flags: this is the failure the fix removes.
    old_flags = ["--json", "--fields", "paths,endpoints"]
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(Path(tmp, "home"))
        with mock.patch.object(flow, "bearings_flags", return_value=old_flags):
            snap = flow.bearings_snapshot(home)
        assert len(snap["gates"]) == 20, len(snap["gates"])
        assert len(snap["in_flight"]) == 20, len(snap["in_flight"])


if __name__ == "__main__":
    test_default_flags_lift_in_flight_and_queued_bounds()
    test_fm_flow_all_also_lifts_landed()
    test_open_work_above_twenty_reaches_the_board()
    test_legacy_producer_keeps_home_with_its_own_bound()
    test_real_producer_shows_every_open_row()
    test_counterfactual_old_flags_drop_open_work()
    print("ok")
