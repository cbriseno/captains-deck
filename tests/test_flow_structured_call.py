#!/usr/bin/env python3
"""A captain call row's structured `call` object becomes real dialog choices.

Firstmate exports `call: {question, options:[{id,label,detail}], recommended,
context, link}` beside the legacy `reason`. The board reads options only from
that object, bounds every field, and leaves rows without it unchanged.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("flow_tui", ROOT / "scripts" / "flow_tui.py")
flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)

CALL = {
    "question": "Ship the new sample flag to everyone?",
    "options": [
        {"id": "staged", "label": "Roll out in stages", "detail": "Ten percent first"},
        {"id": "all", "label": "Everyone at once"},
    ],
    "recommended": "staged",
    "context": "The sample flag passed its checks.",
    "link": "https://example.com/report",
}


class StructuredCallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = flow.Home("fixture", self.tmp.name, "captain")

    def cards(self, **row):
        entry = {"id": "ask", "title": "Pick the sample rollout", "owner": "(main)",
                 "reason": "A long paragraph that already lists options A, B and C in prose.", **row}
        snap = {"decisions_open": [entry], "gates": [dict(entry, id="gate")]}
        cols, _ = flow.make_cards(self.home, snap, {}, {}, {}, [self.home], reveal_answered=True)
        return cols["captains_call"][0], cols["charted"][0]

    def test_rows_carry_the_normalized_call_and_legacy_rows_carry_none(self):
        call, gate = self.cards(call=CALL)
        self.assertEqual(call.call["options"], [
            {"id": "staged", "label": "Roll out in stages", "detail": "Ten percent first"},
            {"id": "all", "label": "Everyone at once", "detail": ""}])
        self.assertEqual(call.call["recommended"], "staged")
        self.assertEqual(gate.call, call.call)
        legacy, legacy_gate = self.cards()
        self.assertEqual((legacy.call, legacy_gate.call), ({}, {}))

    def test_fields_are_bounded_and_bad_options_are_dropped(self):
        raw = {"question": "Q" * 400 + "\nsecond line", "context": "C" * 400,
               "options": [{"id": "ok", "label": "L" * 90, "detail": "D" * 300},
                           {"id": "ok", "label": "duplicate"}, {"id": "reconcile", "label": "reserved"},
                           {"id": "has space", "label": "bad id"}, {"id": 7}, "text", {"label": "no id"}],
               "recommended": "missing"}
        call = flow.structured_call(raw)
        self.assertEqual([o["id"] for o in call["options"]], ["ok"])
        self.assertLessEqual(len(call["question"]), flow.CALL_LIMITS["question"])
        self.assertNotIn("\n", call["question"])
        self.assertLessEqual(len(call["context"]), flow.CALL_LIMITS["context"])
        self.assertLessEqual(len(call["options"][0]["label"]), flow.CALL_LIMITS["label"])
        self.assertLessEqual(len(call["options"][0]["detail"]), flow.CALL_LIMITS["detail"])
        self.assertEqual(call["recommended"], "")
        for empty in (None, "text", [], {}, {"context": "only context"}, {"options": [{"label": "no id"}]}):
            self.assertEqual(flow.structured_call(empty), {}, empty)

    def test_dialog_content_offers_the_call_options_ahead_of_prose(self):
        card, _ = self.cards(call=CALL)
        content = flow.decision_card_content(card, [self.home], self.home)
        self.assertEqual([o["value"] for o in content["options"]], ["staged", "all", "reconcile"])
        self.assertEqual(content["options"][0]["hint"], "Ten percent first")
        self.assertEqual(content["recommend"], "staged")
        self.assertEqual(content["decide"], CALL["question"])
        self.assertEqual(content["about"], CALL["context"])
        self.assertIn("options A, B and C", content["background"])
        self.assertEqual(content["link"], CALL["link"])
        self.assertTrue(content["freeform"])
        dialog = flow.DecisionDialog(content)
        self.assertEqual(dialog.selected_option()["value"], "staged")
        self.assertEqual(dialog.keyed_line().split("\t")[1], "staged")

    def test_legacy_row_keeps_the_own_words_flow_without_invented_options(self):
        card, _ = self.cards()
        content = flow.decision_card_content(card, [self.home], self.home)
        self.assertEqual([o["value"] for o in content["options"]], ["reconcile"])
        self.assertEqual(content["about"], "A long paragraph that already lists options A, B and C in prose.")
        self.assertEqual((content["background"], content["link"], content["recommend"]), ("", "", ""))
        self.assertEqual(content["decide"], "Answer in your own words, or ask Firstmate to check again first.")

    def test_a_question_without_options_still_leads_with_the_question(self):
        card, _ = self.cards(call={"question": "Is the sample ready?"})
        content = flow.decision_card_content(card, [self.home], self.home)
        self.assertEqual(content["decide"], "Is the sample ready?")
        self.assertEqual([o["value"] for o in content["options"]], ["reconcile"])


if __name__ == "__main__":
    unittest.main()
