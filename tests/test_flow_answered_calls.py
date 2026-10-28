#!/usr/bin/env python3
"""The answered-call ledger keeps an answered Captain's Call off the board.

Firstmate keeps a live captain hold after a keyed answer, so the answered row
returns to Captain's Call with no new question and the calls pile up. The deck
records the answered question and hides the row until its owner takes the work.

Run: python3 tests/test_flow_answered_calls.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FLOW = ROOT / "scripts" / "flow_tui.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# The ledger and the setting are file-backed, so both point at throwaway
# directories: a test must never touch the captain's own state.
STATE_DIR = Path(tempfile.mkdtemp(prefix="deck-answered-state-"))
CONFIG_DIR = Path(tempfile.mkdtemp(prefix="deck-answered-config-"))
HOME = Path(tempfile.mkdtemp(prefix="deck-answered-home-")) / "home"
shutil.copytree(FIXTURES / "home", HOME)
CARD_STORE = HOME / "state" / "decision-cards" / "decision-1.json"
FIXTURE_STAMP = "2026-01-01T00:00:00Z"

os.environ["HERDR_PLUGIN_STATE_DIR"] = str(STATE_DIR)
os.environ["HERDR_PLUGIN_CONFIG_DIR"] = str(CONFIG_DIR)
os.environ.pop("FM_FLOW_HIDE_ANSWERED", None)
os.environ.pop("FM_FLOW_ANSWER_MATCH", None)

FAILURES: list[str] = []


def load_flow():
    spec = importlib.util.spec_from_file_location("flow_tui_answered", FLOW)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["flow_tui_answered"] = mod
    spec.loader.exec_module(mod)
    return mod


flow = load_flow()


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"ok - {msg}")
    else:
        FAILURES.append(msg)
        print(f"NOT OK - {msg}")


def cards() -> tuple[dict, dict]:
    home = flow.Home("captain", str(HOME), "captain")
    snap = json.loads((FIXTURES / "bearings_snapshot.json").read_text())
    meta_index = flow.build_meta_index([home])
    return flow.make_cards(home, snap, meta_index, {}, {}, [home])


def keys(cols: dict) -> list[str]:
    return [card.task for card in cols["captains_call"]]


def set_card_stamp(stamp: str) -> None:
    raw = json.loads(CARD_STORE.read_text())
    raw["generated"] = stamp
    CARD_STORE.write_text(json.dumps(raw))


def record_answer(answered_at: float | None = None) -> dict:
    """Record the board's one call as answered, and return its dialog content."""
    home = flow.Home("captain", str(HOME), "captain")
    cols, _ = cards()
    content = flow.decision_card_content(cols["captains_call"][0], [home], home)
    flow.save_answered_calls({})
    dialog = flow.DecisionDialog(content)
    dialog.note = "prod"
    flow.record_answered_call(dialog)
    if answered_at is not None:
        records = flow.load_answered_calls()
        records[flow.answered_call_key(str(HOME), "decision-1")]["answered_at"] = answered_at
        flow.save_answered_calls(records)
    return content


def test_digest_is_the_question_identity() -> None:
    options = [{"value": "prod"}, {"value": "staging"}]
    base = flow.call_digest("k", "Title", options, "release")
    check(
        base == flow.call_digest("k", "Title", list(reversed(options)), "release"),
        "the digest ignores the order of the options",
    )
    check(
        base != flow.call_digest("k", "Title", [{"value": "prod"}], "release"),
        "a changed option set changes the digest",
    )
    check(
        base != flow.call_digest("k", "Title", options, "done"),
        "a changed close mode changes the digest",
    )
    check(
        flow.call_digest("k", "Title", options, "release") == base,
        "the same question keeps one digest",
    )


def test_ledger_round_trip() -> None:
    key = flow.answered_call_key(str(HOME), "decision-1")
    flow.save_answered_calls(
        {key: {"home": str(HOME), "answered_at": time.time(), "answer": "prod"}}
    )
    check(
        flow.load_answered_calls().get(key, {}).get("answer") == "prod",
        "the ledger round-trips",
    )
    check(
        str(flow.answered_ledger_path()).startswith(str(STATE_DIR)),
        "the ledger lives in the plugin state dir",
    )


def hidden_flags(cols: dict) -> dict[str, bool]:
    return {
        card.task: bool(getattr(card, "answered_hidden", False))
        for card in cols["captains_call"]
    }


def test_an_answered_call_leaves_the_board() -> None:
    record_answer()
    cols, totals = cards()
    check(
        keys(cols) == ["decision-1"],
        "the answered row stays in the column, so a reveal can bring it back",
    )
    check(hidden_flags(cols)["decision-1"] is True, "the answered row is marked hidden")
    check(totals["captains_call"] == 0, "the column total counts only actionable calls")
    check(totals["captains_call_hidden"] == 1, "the hidden row is counted for the footer")


def test_a_reveal_clears_the_mark() -> None:
    record_answer()
    home = flow.Home("captain", str(HOME), "captain")
    snap = json.loads((FIXTURES / "bearings_snapshot.json").read_text())
    meta_index = flow.build_meta_index([home])
    cols, totals = flow.make_cards(
        home, snap, meta_index, {}, {}, [home], reveal_answered=True
    )
    check(hidden_flags(cols)["decision-1"] is False, "A reveal clears the hidden mark")
    check(totals["captains_call"] == 1, "a revealed call counts as actionable again")


def test_a_recomposed_card_and_the_match_switch() -> None:
    # Measured on 2026-10-16: the board rewrote the same three cards at 12:09 and
    # again at 12:27 with unchanged content, so the stamp moved on its own.
    record_answer()
    set_card_stamp("2099-01-01T00:00:00Z")
    cols, _ = cards()
    check(
        hidden_flags(cols)["decision-1"] is False,
        "a re-composed stamp brings the row back under the stamp rule",
    )
    os.environ["FM_FLOW_ANSWER_MATCH"] = "content"
    try:
        cols, _ = cards()
        check(
            hidden_flags(cols)["decision-1"] is True,
            "FM_FLOW_ANSWER_MATCH=content survives the re-compose",
        )
    finally:
        os.environ.pop("FM_FLOW_ANSWER_MATCH", None)


def test_a_spent_record_stops_hiding() -> None:
    set_card_stamp(FIXTURE_STAMP)
    record_answer(answered_at=time.time() - 8 * 24 * 3600)
    cols, _ = cards()
    check(
        hidden_flags(cols)["decision-1"] is False,
        "a record older than seven days stops hiding the row",
    )


def test_another_homes_answer_does_not_hide_this_call() -> None:
    set_card_stamp(FIXTURE_STAMP)
    record_answer()
    records = flow.load_answered_calls()
    records[flow.answered_call_key(str(HOME), "decision-1")]["home"] = str(HOME.parent / "other-home")
    flow.save_answered_calls(records)
    cols, totals = cards()
    check(
        hidden_flags(cols)["decision-1"] is False,
        "another home's answer to a same-named call does not hide this one",
    )
    check(totals["captains_call"] == 1, "the call still counts as actionable")


def test_a_record_whose_row_left_the_column_is_dropped() -> None:
    key = flow.answered_call_key(str(HOME), "another-task")
    flow.save_answered_calls(
        {key: {"home": str(HOME), "answered_at": time.time()}}
    )
    cards()
    check(
        key not in flow.load_answered_calls(),
        "a record whose row left the column is dropped",
    )


def test_record_from_a_dialog() -> None:
    set_card_stamp(FIXTURE_STAMP)
    flow.save_answered_calls({})
    home = flow.Home("captain", str(HOME), "captain")
    cols, _ = cards()
    content = flow.decision_card_content(cols["captains_call"][0], [home], home)
    dialog = flow.DecisionDialog(content)
    dialog.selected = next(
        (i for i, opt in enumerate(dialog.options) if opt["value"] != "reconcile"), -1
    )
    dialog.note = "shipping prod"
    flow.record_answered_call(dialog)
    records = flow.load_answered_calls()
    record = records.get(flow.answered_call_key(str(HOME), "decision-1")) or {}
    check("decision-1" not in records, "new answers use only home/task keys")
    check(
        record.get("generated") == content.get("stamp"),
        "the record keeps the card's generated stamp",
    )
    check(
        record.get("digest")
        == flow.call_digest(dialog.key, dialog.title, dialog.options, dialog.close),
        "the record keeps the question's digest",
    )
    check(
        record.get("home") == str(HOME) and bool(record.get("answer")),
        "the record keeps the owning home and the answer",
    )


def test_same_named_answers_and_legacy_records() -> None:
    with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, HERDR_PLUGIN_STATE_DIR=root):
        homes = [flow.Home(name, str(Path(root, name)), "captain") for name in ("A", "B")]
        snapshots = {}
        for home in homes:
            path = Path(home.path, "state/decision-cards/docs.json")
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"generated": home.label + "-stamp", "card": {
                "key": "docs", "title": home.label + " question",
                "options": [{"value": home.label, "label": home.label + " answer"}]
            }}))
            snapshots[home.path] = {"decisions_open": [{"id": "docs", "owner": "(main)", "title": "Durable title"}]}

        def columns(home, crew=None):
            return flow.make_cards(home, snapshots[home.path], {}, {}, {}, crew or homes, bearings=snapshots)

        def answer(home):
            cols, _ = columns(home)
            content = flow.decision_card_content(cols["captains_call"][0], homes, home)
            dialog = flow.DecisionDialog(content)
            dialog.selected = 0
            flow.record_answered_call(dialog)

        with patch.object(flow, "bearings_snapshot", side_effect=AssertionError("unexpected snapshot walk")):
            answer(homes[0])
            cols, totals = columns(homes[1])
            check(not cols["captains_call"][0].answered_hidden and totals["captains_call"] == 1,
                  "answering A/docs leaves B/docs actionable")
            answer(homes[1])
            records = flow.load_answered_calls()
            check(len(records) == 2 and "docs" not in records,
                  "both answers survive a ledger reload under home/task keys")
            for match in ("generated", "content"):
                with patch.dict(os.environ, FM_FLOW_ANSWER_MATCH=match):
                    parts = []
                    for home in homes:
                        cols, totals = columns(home)
                        check(cols["captains_call"][0].answered_hidden and totals["captains_call"] == 0,
                              f"{home.label}/docs stays answered with {match} matching")
                        parts.append((home, cols, totals))
                    _, totals = flow.merge_fleet_columns(parts)
                    check(totals["captains_call"] == 0 and totals["captains_call_hidden"] == 2,
                          f"All counts both answered homes with {match} matching")
            legacy = {"docs": records[flow.answered_call_key(homes[0].path, "docs")]}
            Path(flow.answered_ledger_path()).write_text(json.dumps(legacy))
            for home in homes:
                cols, _ = columns(home)
                check(not cols["captains_call"][0].answered_hidden,
                      f"ambiguous legacy docs does not hide {home.label}'s call")
            snapshots[homes[1].path] = {"decisions_open": []}
            cols, _ = columns(homes[0])
            check(cols["captains_call"][0].answered_hidden,
                  "a legacy record hides a task held by exactly one current home")
            cols, _ = columns(homes[0], [homes[0]])
            check(cols["captains_call"][0].answered_hidden,
                  "a legacy record remains compatible with a single-home install")
            answer(homes[0])
            check(set(flow.load_answered_calls()) == {flow.answered_call_key(homes[0].path, "docs")},
                  "answering a legacy call replaces its task-only key")
            Path(flow.answered_ledger_path()).write_text(json.dumps(legacy))
            snapshots[homes[1].path] = {}
            cols, _ = columns(homes[0])
            check(not cols["captains_call"][0].answered_hidden,
                  "an unavailable home cannot establish legacy uniqueness")
            flow.save_answered_calls(records)
            snapshots[homes[0].path] = {"decisions_open": []}
            columns(homes[0])
            remaining = flow.load_answered_calls()
            check(set(remaining) == {flow.answered_call_key(homes[1].path, "docs")},
                  "cleanup removes only the departed home's record even with no remaining calls")


def test_the_setting_can_turn_the_hiding_off() -> None:
    (CONFIG_DIR / "hide_answered_calls").write_text("0\n")
    check(flow.hide_answered_default() is False, "hide_answered_calls=0 turns it off")
    (CONFIG_DIR / "hide_answered_calls").write_text("1\n")
    check(flow.hide_answered_default() is True, "hide_answered_calls=1 turns it on")


def main() -> int:
    test_digest_is_the_question_identity()
    test_ledger_round_trip()
    test_an_answered_call_leaves_the_board()
    test_a_reveal_clears_the_mark()
    test_a_recomposed_card_and_the_match_switch()
    test_a_spent_record_stops_hiding()
    test_another_homes_answer_does_not_hide_this_call()
    test_a_record_whose_row_left_the_column_is_dropped()
    test_record_from_a_dialog()
    test_same_named_answers_and_legacy_records()
    test_the_setting_can_turn_the_hiding_off()
    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
