"""Fleet All crew tab: merge planned/running columns across homes."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FLOW = ROOT / "scripts" / "flow_tui.py"


def load_flow():
    spec = importlib.util.spec_from_file_location("flow_tui", FLOW)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["flow_tui"] = mod
    spec.loader.exec_module(mod)
    return mod


flow = load_flow()
Home = flow.Home
Card = flow.Card
merge_fleet_columns = flow.merge_fleet_columns
fleet_planned_running_count = flow.fleet_planned_running_count
inject_all_crew = flow.inject_all_crew
ALL_CREW_LABEL = flow.ALL_CREW_LABEL


def _card(
    task: str,
    home_path: str,
    bucket: str = "underway",
    *,
    task_id: str = "",
    doing: str = "",
    worktree: str = "",
    live: str = "",
    pane: str = "",
) -> Card:
    c = Card()
    c.task = task
    c.id = task_id or task
    c.home_path = home_path
    c.bucket = bucket
    c.doing = doing
    c.worktree = worktree
    c.live_status = live
    c.pane_id = pane
    c.run_elapsed = ""
    c.run_tokens = ""
    return c


def test_inject_all_crew_prepends_tab():
    homes = [Home("captain", "/a", "captain"), Home("mate", "/b", "secondmate")]
    out = inject_all_crew(homes)
    assert out[0].label == ALL_CREW_LABEL
    assert out[0].kind == "all"


def test_merge_retains_bound_landed_and_sorts_active_by_crew():
    captain = Home("captain", "/captain", "captain")
    mate = Home("2ndmate-x", "/mate", "secondmate")
    cols_c = {"charted": [_card("a", "/captain", "charted")], "landed": [_card("old", "/captain", "landed")]}
    cols_m = {"underway": [_card("b", "/mate")]}
    totals_c = {"charted": 1, "landed": 1}
    totals_m = {"underway": 1}
    cols, totals = merge_fleet_columns(
        [(captain, cols_c, totals_c), (mate, cols_m, totals_m)]
    )
    assert cols["landed"] == cols_c["landed"]
    assert [c.task for c in cols["charted"]] == ["a"]
    assert [c.task for c in cols["underway"]] == ["b"]
    assert totals["landed"] == 1


def test_fleet_planned_running_count_excludes_landed():
    homes = inject_all_crew([Home("captain", "/a", "captain")])
    counts = {"captain": 5}
    landed = {"captain": 2}
    assert fleet_planned_running_count(homes, counts, landed) == 3


def test_merge_dedupes_captains_call_by_task():
    captain = Home("captain", "/captain", "captain")
    mate = Home("2ndmate-x", "/mate", "secondmate")
    dup_a = _card("fpr-issue-29", "/mate", "captains_call")
    dup_a.owner = "demo-lane"
    dup_b = _card("fpr-issue-29", "/mate", "captains_call")
    dup_b.owner = "(main)"
    dup_a.delivery_identity = dup_b.delivery_identity = "/mate"
    cols_c = {"captains_call": [dup_a]}
    cols_m = {"captains_call": [dup_b]}
    totals_c = {"captains_call": 1}
    totals_m = {"captains_call": 1}
    cols, totals = merge_fleet_columns(
        [(captain, cols_c, totals_c), (mate, cols_m, totals_m)]
    )
    assert [c.task for c in cols["captains_call"]] == ["fpr-issue-29"]
    assert cols["captains_call"][0].owner == "demo-lane"
    assert totals["captains_call"] == 1


def _held(task: str, owner: str = "(main)") -> dict:
    return {"decisions_open": [{"id": task, "title": "Pick a color", "owner": owner}]}


def _call_columns(home: Home, snap: dict, homes: list[Home]) -> tuple[dict, dict]:
    # reveal_answered keeps the user's answered-call ledger out of the test.
    return flow.make_cards(home, snap, {}, {}, {}, homes, reveal_answered=True)


def test_merge_keeps_same_named_calls_from_independent_homes(tmp_path):
    claude = Home("claude", str(tmp_path / "claude"), "captain")
    codex = Home("codex", str(tmp_path / "codex"), "captain")
    homes = [claude, codex]
    parts = []
    for home in homes:
        cols, totals = _call_columns(home, _held("demo-color"), homes)
        parts.append((home, cols, totals))
    cols, totals = merge_fleet_columns(parts)
    assert [c.home_path for c in cols["captains_call"]] == [claude.path, codex.path]
    assert totals["captains_call"] == 2


def test_all_dialog_uses_each_homes_question_sources(tmp_path):
    for representation in ("store", "board", "payload"):
        homes = [Home(name, str(tmp_path / representation / name), "captain") for name in ("A", "B")]
        parts = []
        sources = []
        for home in homes:
            raw = {"key": "docs", "title": home.label + " question", "about": home.label + " context",
                   "options": [{"value": home.label, "label": home.label + " answer"}]}
            if representation == "store":
                path = Path(home.path, "state/decision-cards/docs.json")
                text = json.dumps({"generated": home.label + "-stamp", "card": raw})
            else:
                data = json.dumps({"captains_call": [raw]})
                if representation == "board":
                    path = Path(home.path, ".lavish/bearings-board.html")
                    text = '<script id="bearings-data">' + data + '</script>'
                else:
                    path = Path(home.path, "data/bearings-board-payload-1.json")
                    text = data
            path.parent.mkdir(parents=True)
            path.write_text(text)
            sources.append(path)
            cols, totals = _call_columns(home, _held("docs"), homes)
            parts.append((home, cols, totals))
        cols, _ = merge_fleet_columns(parts)
        snapshot = flow.Snapshot()
        snapshot.home = Home("All", "", "all")
        snapshot.homes = flow.inject_all_crew(homes)
        snapshot.cols = cols
        ui = flow.UI()
        ui.collector = SimpleNamespace(snapshot=lambda: snapshot)
        for card, home in zip(cols["captains_call"], homes):
            ui.open_decision(card)
            assert ui.dialog.title == home.label + " question"
            assert ui.dialog.about == home.label + " context"
            assert ui.dialog.home_path == home.path
            assert ui.dialog.options[0]["value"] == home.label
        sources[1].unlink()
        ui.open_decision(cols["captains_call"][1])
        assert ui.dialog.title == "Pick a color"
        assert [o["value"] for o in ui.dialog.options] == ["reconcile"]
        assert ui.dialog.home_path == homes[1].path


def test_legacy_matching_reuses_collector_snapshots_on_repaint(tmp_path):
    homes = [Home(name, str(tmp_path / name), "captain") for name in ("A", "B")]
    now = time.time()
    collector = flow.Collector()
    for home in homes:
        path = Path(home.path, "state/decision-cards/docs.json")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"generated": "stamp", "card": {"title": home.label + " question"}}))
        collector._raw[home.label] = (_held("docs"), now, home.path)
    crew = flow.inject_all_crew(homes)
    with patch.dict(os.environ, HERDR_PLUGIN_STATE_DIR=str(tmp_path / "ledger"),
                    FM_FLOW_HIDE_ANSWERED="1", FM_FLOW_ANSWER_MATCH="generated"):
        flow.save_answered_calls({"docs": {"home": homes[0].path, "generated": "stamp", "answered_at": now}})
        with patch.object(flow, "bearings_snapshot", side_effect=AssertionError("unexpected snapshot walk")):
            for _ in range(2):
                collector._publish_fleet(crew, {}, {}, now)
                assert collector.snapshot().totals["captains_call"] == 2
            collector._raw[homes[1].label] = ({"decisions_open": []}, now, homes[1].path)
            for _ in range(2):
                collector._publish_fleet(crew, {}, {}, now)
                assert collector.snapshot().totals["captains_call"] == 0
                assert collector.snapshot().hidden_calls == 1
            collector._raw.pop(homes[1].label)
            _, cols, totals, _, _ = collector._load_bearings(homes[0], now, {}, {}, crew)
            assert not cols["captains_call"][0].answered_hidden
            assert totals["captains_call"] == 1
        with patch.object(flow, "bearings_snapshot", return_value=_held("docs")) as walk:
            collector._load_bearings(homes[0], now + flow._bearings_ttl() + 1, {}, {}, crew)
            walk.assert_called_once_with(homes[0])


def test_parent_content_uses_cached_ownership_for_fingerprints_and_dialogs(tmp_path):
    captain = Home("captain", str(tmp_path / "captain"), "captain")
    mate = Home("2ndmate-lane", str(tmp_path / "mate"), "secondmate", mate_id="lane")
    homes = flow.inject_all_crew([captain, mate])
    path = Path(captain.path, "state/decision-cards/docs.json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"generated": "parent-stamp", "card": {
        "title": "The delegated question", "options": [{"value": "yes", "label": "Proceed"}]
    }}))
    now = time.time()
    collector = flow.Collector()
    collector._raw = {
        captain.label: (_held("docs", "lane"), now, captain.path),
        mate.label: (_held("docs"), now, mate.path),
    }
    with patch.dict(os.environ, HERDR_PLUGIN_STATE_DIR=str(tmp_path / "ledger"), FM_FLOW_HIDE_ANSWERED="1"):
        with patch.object(flow, "bearings_snapshot", side_effect=AssertionError("unexpected snapshot walk")):
            collector._publish_fleet(homes, {}, {}, now)
            ui = flow.UI()
            ui.collector = collector
            ui.open_decision(collector.snapshot().cols["captains_call"][0])
            assert ui.dialog.title == "The delegated question"
            assert ui.dialog.home_path == mate.path
            ui.dialog.selected = 0
            flow.record_answered_call(ui.dialog)
            for match in ("generated", "content"):
                with patch.dict(os.environ, FM_FLOW_ANSWER_MATCH=match):
                    collector._publish_fleet(homes, {}, {}, now)
                    snapshot = collector.snapshot()
                    assert snapshot.totals["captains_call"] == 0
                    assert snapshot.hidden_calls == 1
                    ui.open_decision(snapshot.cols["captains_call"][0])
                    assert ui.dialog.stamp == "parent-stamp"
                    assert ui.dialog.options[0]["value"] == "yes"
            without_evidence = flow.decision_card_content(snapshot.cols["captains_call"][0], homes, snapshot.home)
            assert without_evidence["title"] == "Pick a color"
            assert [o["value"] for o in without_evidence["options"]] == ["reconcile"]


def test_legacy_answer_ignores_expired_inactive_and_retained_snapshots(tmp_path):
    homes = [Home(name, str(tmp_path / name), "captain") for name in ("A", "B")]
    a, b = homes
    store = Path(a.path, "state/decision-cards/docs.json")
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"generated": "stamp", "card": {"title": "A question"}}))
    collector = flow.Collector()
    collector._raw = {a.label: (_held("docs"), 1000.0, a.path),
                      b.label: ({"decisions_open": []}, 1000.0, b.path)}
    current = {a.path: _held("docs"), b.path: _held("docs")}
    with patch.dict(os.environ, HERDR_PLUGIN_STATE_DIR=str(tmp_path / "ledger"),
                    FM_FLOW_HIDE_ANSWERED="1", FM_FLOW_ANSWER_MATCH="generated"):
        with patch.object(flow.time, "time", return_value=1000.0) as clock, \
                patch.object(flow, "_bearings_ttl", return_value=60.0), \
                patch.object(flow, "bearings_snapshot", side_effect=lambda home: current[home.path]) as walk:
            flow.save_answered_calls({"docs": {"home": a.path, "generated": "stamp", "answered_at": 1000.0}})
            _, cols, totals, _, _ = collector._load_bearings(a, clock.return_value, {}, {}, homes)
            assert cols["captains_call"][0].answered_hidden
            assert totals["captains_call"] == 0
            walk.assert_not_called()
            clock.return_value = 1061.0
            _, cols, totals, _, _ = collector._load_bearings(a, clock.return_value, {}, {}, homes)
            assert not cols["captains_call"][0].answered_hidden
            assert totals["captains_call"] == 1
            walk.assert_called_once_with(a)
            collector._load_bearings(b, clock.return_value, {}, {}, homes)
            _, cols, _, _, _ = collector._load_bearings(a, clock.return_value, {}, {}, homes)
            assert not cols["captains_call"][0].answered_hidden
            assert walk.call_count == 2
            clock.return_value = 1122.0
            current[b.path] = {"decisions_open": []}
            current[a.path] = {}
            collector._load_bearings(b, clock.return_value, {}, {}, homes)
            _, cols, totals, _, _ = collector._load_bearings(a, clock.return_value, {}, {}, homes)
            assert not cols["captains_call"][0].answered_hidden
            assert totals["captains_call"] == 1
            assert collector._raw[a.label][1] == 1061.0


def test_parent_ownership_expires_in_published_dialogs_and_fingerprints(tmp_path):
    captain = Home("captain", str(tmp_path / "captain"), "captain")
    mate = Home("2ndmate-lane", str(tmp_path / "mate"), "secondmate", mate_id="lane")
    homes = flow.inject_all_crew([captain, mate])
    store = Path(captain.path, "state/decision-cards/docs.json")
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"generated": "stamp", "card": {
        "title": "Delegated question", "options": [{"value": "yes", "label": "Proceed"}]
    }}))
    raw = {captain.path: _held("docs", "lane"), mate.path: _held("docs")}
    collector = flow.Collector()
    collector._raw = {home.label: (raw[home.path], 1000.0, home.path) for home in (captain, mate)}
    with patch.dict(os.environ, HERDR_PLUGIN_STATE_DIR=str(tmp_path / "ledger"),
                    FM_FLOW_HIDE_ANSWERED="1", FM_FLOW_ANSWER_MATCH="generated"):
        with patch.object(flow.time, "time", return_value=1000.0) as clock, \
                patch.object(flow, "_bearings_ttl", return_value=60.0), \
                patch.object(flow, "bearings_snapshot", side_effect=lambda home: raw[home.path]) as walk:
            collector._publish_fleet(homes, {}, {}, clock.return_value)
            ui = flow.UI()
            ui.collector = collector
            card = collector.snapshot().cols["captains_call"][0]
            ui.open_decision(card)
            assert ui.dialog.options[0]["value"] == "yes"
            ui.dialog.selected = 0
            flow.record_answered_call(ui.dialog)
            clock.return_value = 1061.0
            ui.open_decision(card)
            assert [o["value"] for o in ui.dialog.options] == ["reconcile"]
            walk.assert_not_called()
            _, cols, totals, _, _ = collector._load_bearings(mate, clock.return_value, {}, {}, homes)
            assert not cols["captains_call"][0].answered_hidden
            assert totals["captains_call"] == 1
            walk.assert_called_once_with(mate)
            collector._load_bearings(captain, clock.return_value, {}, {}, homes)
            _, cols, totals, _, _ = collector._load_bearings(mate, clock.return_value, {}, {}, homes)
            collector._publish(collector._snapshot(homes, mate, cols, {}, {}, totals))
            ui.open_decision(cols["captains_call"][0])
            assert ui.dialog.options[0]["value"] == "yes"
            assert cols["captains_call"][0].answered_hidden
            assert walk.call_count == 2


def test_merge_folds_parent_mirror_of_a_mate_call(tmp_path):
    captain = Home("captain", str(tmp_path / "captain"), "captain")
    mate = Home("2ndmate-lane", str(tmp_path / "mate"), "secondmate", mate_id="lane")
    homes = [captain, mate]
    cols_c, totals_c = _call_columns(captain, _held("demo-color", "lane"), homes)
    cols_m, totals_m = _call_columns(mate, _held("demo-color"), homes)
    cols, totals = merge_fleet_columns(
        [(captain, cols_c, totals_c), (mate, cols_m, totals_m)]
    )
    assert [(c.home_path, c.owner) for c in cols["captains_call"]] == [(mate.path, "lane")]
    assert totals["captains_call"] == 1
    for home_cols in (cols_c, cols_m):
        home_cols["captains_call"][0].answered_hidden = True
    cols, totals = merge_fleet_columns(
        [(captain, cols_c, totals_c), (mate, cols_m, totals_m)]
    )
    assert len(cols["captains_call"]) == 1
    assert totals["captains_call"] == 0
    assert totals["captains_call_hidden"] == 1


def test_merge_keeps_calls_with_an_unknown_answering_home(tmp_path):
    captain = Home("captain", str(tmp_path / "captain"), "captain")
    mate = Home("2ndmate-lane", str(tmp_path / "mate"), "secondmate", mate_id="lane")
    homes = [captain, mate]
    cols_c, totals_c = _call_columns(captain, _held("demo-color", "gone"), homes)
    cols_m, totals_m = _call_columns(mate, _held("demo-color"), homes)
    cols, totals = merge_fleet_columns(
        [(captain, cols_c, totals_c), (mate, cols_m, totals_m)]
    )
    assert sorted(c.home_path for c in cols["captains_call"]) == ["", mate.path]
    assert totals["captains_call"] == 2


def test_merge_folds_parent_mirror_into_owner_row():
    captain = Home("captain", "/captain", "captain")
    mate = Home("2ndmate-racket-finder", "/mate2", "secondmate", mate_id="racket-finder")
    mirror = _card(
        "fpr-issue-217",
        "/captain",
        task_id="racket-finder/fpr-issue-217",
        pane="w3A:p2",
        live="working",
    )
    owner = _card("fpr-issue-217", "/mate2", pane="w3A:p2", live="working")
    cols, totals = merge_fleet_columns(
        [
            (captain, {"underway": [mirror]}, {"underway": 1}),
            (mate, {"underway": [owner]}, {"underway": 1}),
        ]
    )
    assert [c.id for c in cols["underway"]] == ["fpr-issue-217"]
    assert cols["underway"][0].home_path == "/mate2"
    assert totals["underway"] == 1


def test_merge_folds_mirror_into_owner_bucket():
    captain = Home("captain", "/captain", "captain")
    mate = Home("2ndmate-racket-finder", "/mate2", "secondmate", mate_id="racket-finder")
    mirror = _card(
        "fpr-sentry-sourcemaps",
        "/captain",
        "underway",
        task_id="racket-finder/fpr-sentry-sourcemaps",
    )
    owner = _card("fpr-sentry-sourcemaps", "/mate2", "awaiting_merge")
    cols, totals = merge_fleet_columns(
        [
            (captain, {"underway": [mirror]}, {"underway": 1}),
            (mate, {"awaiting_merge": [owner]}, {"awaiting_merge": 1}),
        ]
    )
    assert cols["underway"] == []
    assert [c.id for c in cols["awaiting_merge"]] == ["fpr-sentry-sourcemaps"]
    assert totals["underway"] == 0
    assert totals["awaiting_merge"] == 1


def test_merge_keeps_same_named_tasks_without_evidence():
    one = Home("2ndmate-letspadel-app", "/mate1", "secondmate", mate_id="letspadel-app")
    two = Home("2ndmate-racket-finder", "/mate2", "secondmate", mate_id="racket-finder")
    cols, totals = merge_fleet_columns(
        [
            (
                one,
                {"underway": [_card("issue-42", "/mate1", worktree="/wt/one")]},
                {"underway": 1},
            ),
            (
                two,
                {"underway": [_card("issue-42", "/mate2", worktree="/wt/two")]},
                {"underway": 1},
            ),
        ]
    )
    assert [c.home_path for c in cols["underway"]] == ["/mate1", "/mate2"]
    assert totals["underway"] == 2


def test_merge_folds_rows_sharing_a_run_id():
    one = Home("captain", "/captain", "captain")
    two = Home("2ndmate-x", "/mate", "secondmate", mate_id="x")
    doing = "validating (running) \u00b7 run: 01M3D485GTH0DCJ47D6DEMFCD4"
    cols, totals = merge_fleet_columns(
        [
            (one, {"underway": [_card("issue-7", "/captain", doing=doing)]}, {"underway": 1}),
            (two, {"underway": [_card("issue-7", "/mate", doing=doing)]}, {"underway": 1}),
        ]
    )
    assert len(cols["underway"]) == 1
    assert totals["underway"] == 1


def test_merge_folds_one_home_listing_task_twice():
    home = Home("captain", "/captain", "captain")
    gate = _card("issue-9", "/captain", "charted")
    live = _card("issue-9", "/captain", "underway")
    cols, totals = merge_fleet_columns(
        [
            (
                home,
                {"charted": [gate], "underway": [live]},
                {"charted": 1, "underway": 1},
            )
        ]
    )
    assert cols["charted"] == []
    assert [c.bucket for c in cols["underway"]] == ["underway"]
    assert totals["charted"] == 0
    assert totals["underway"] == 1


def test_fleet_planned_running_count_subtracts_folded_duplicates():
    homes = inject_all_crew(
        [Home("captain", "/a", "captain"), Home("mate", "/b", "secondmate")]
    )
    counts = {"captain": 2, "mate": 2}
    assert fleet_planned_running_count(homes, counts, {}, duplicates=1) == 3
