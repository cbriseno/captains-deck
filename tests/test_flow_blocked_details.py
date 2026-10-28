"""Exercise task transitions through the real log parser, card builder and UI."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("flow_blocked", ROOT / "scripts/flow_tui.py")
flow = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = flow
spec.loader.exec_module(flow)
DIAGNOSTIC = "harness state unavailable (unknown codex-unverified)"


class BlockedDetailsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        (self.path / "state").mkdir()
        self.status = self.path / "state/task.status"
        self.home = flow.Home("crew", str(self.path), "captain")

    def board(self, log="", *, native=None, state="unknown", bucket="in_flight", **entry):
        self.status.write_text(log)
        (self.path / "state/task.meta").write_text("harness=codex\nherdr_pane_id=test:p1\n")
        snap = flow.Snapshot()
        snap.home = self.home
        snap.homes = [self.home]
        row = {"id": "task", "state": state, "doing": DIAGNOSTIC, "name": "Repair identity", **entry}
        with patch.object(flow, "hide_answered_default", return_value=False):
            snap.cols, snap.totals = flow.make_cards(
                self.home, {bucket: [row]}, flow.build_meta_index([self.home]),
                {"test:p1": {"agent_status": native}} if native else {},
                {"test:p1": {}}, [self.home],
            )
        return next(c for cards in snap.cols.values() for c in cards), snap

    def ui(self, snap):
        ui = flow.UI()
        ui.collector.snapshot = lambda: snap
        ui.col_idx = next(i for i, (k, _) in enumerate(ui.columns) if snap.cols.get(k))
        return ui

    def frame(self, ui, snap, width=122, height=38):
        frames = []
        with patch.object(flow.shutil, "get_terminal_size", return_value=os.terminal_size((width, height))), \
                patch.object(ui, "paint", side_effect=lambda lines, *_: frames.append(lines)):
            ui.render(snap, force=True)
        lines = [flow._ANSI_RE.sub("", ln) for ln in frames[0]]
        assert len(lines) == height
        assert all(flow.display_width(ln) <= width for ln in lines)
        return "\n".join(lines)

    def test_working_blocked_release_and_resume_without_changing_buckets(self):
        working = "working [at=100]: Checking identity\n"
        blocked = "blocked [at=101]: [key=identity] inbox012 result incorporated; missing authenticated binding\n"
        card, snap = self.board(working, native="working")
        assert card.badge == "● Herdr working"
        card, snap = self.board(working + blocked, native="working")
        assert card.badge == "⛔ blocked · reported"
        assert card.bucket == "underway" and not snap.cols["captains_call"]
        assert "missing authenticated binding" in flow.UI.card_status_line(card, 200)
        assert "⛔1" in self.frame(self.ui(snap), snap)
        for tail in ("working [at=102]: Trying a different path\n", "done [at=102]: Candidate ready\n"):
            card, _ = self.board(working + blocked + tail, native="working")
            assert "blocked" in card.badge
        release = "resolved [key=identity] [at=103]: dependency supplied; release, not task completion\n"
        card, snap = self.board(working + blocked + release + "working [at=104]: Resuming validation\n", native="working")
        assert card.badge == "● Herdr working" and card.blockers == []
        assert snap.totals["underway"] == 1 and not snap.cols["awaiting_merge"]
        assert "⛔1" not in self.frame(self.ui(snap), snap)

    def test_terminal_decision_fold_matches_task_kind(self):
        # Small private log fixture mirrors the historical post-colon key and
        # actual terminal declaration from parser-reconciliation-028.
        opening = "needs-decision [at=1792808000]: [key=lab-first-turn-evidence] Missing evidence\n"
        for kind in ("ship", "scout", "secondmate"):
            for terminal in ("done", "failed"):
                with self.subTest(kind=kind, terminal=terminal):
                    tail = f"{terminal} [at=1792808703]: ready in branch fm/demo-identity\n"
                    card, _ = self.board(opening + tail, kind=kind, native="idle")
                    expected = ["lab-first-turn-evidence"] if kind == "secondmate" else []
                    self.assertEqual([b["key"] for b in card.blockers], expected)
                    self.assertEqual("decision" in card.badge, kind == "secondmate")
                    # A later declaration reopens even the same key; terminal
                    # closure must be sequential, not a whole-log filter.
                    reopened = "blocked [key=lab-first-turn-evidence] [at=1792808800]: New evidence needed\n"
                    card, _ = self.board(opening + tail + reopened, kind=kind)
                    self.assertEqual(len(card.blockers), 1)
                    self.assertEqual(card.blockers[0]["text"], "New evidence needed")
                    self.assertEqual(card.blockers[0]["at"], 1792808800)

    def test_resolution_only_closes_its_exact_key(self):
        log = ("blocked [at=100] [key=one]: Missing native identity\n"
               "blocked [key=two] [at=101]: Missing test slot\n"
               "resolved [at=102] [key=one]: identity supplied\n"
               "working [at=103]: Candidate ready\n")
        card, _ = self.board(log)
        assert [b["key"] for b in card.blockers] == ["two"]
        assert flow.UI.card_status_line(card, 80) == "Reason: Missing test slot"

    def test_full_reason_survives_progress_filter_and_narrow_card(self):
        reason = "inbox012 result incorporated; native pane wT:p1; " + "missing authenticated binding " * 24 + "END_OF_REASON"
        card, snap = self.board(f"blocked [key=identity] [at=101]: {reason}\n", native="idle")
        assert card.report_text == ""  # still not progress
        assert card.blockers[0]["text"] == reason
        assert len(card.status_text) <= 200
        ui = self.ui(snap)
        short = flow.UI.card_status_line(card, 18)
        assert short.endswith("…") and len(short) <= 18
        flow.parse_input(b"d", ui)
        seen = self.frame(ui, snap, 80, 24)
        while ui.details_scroll < ui.details_max:
            flow.parse_input(b"\x1b[6~", ui)
            seen += self.frame(ui, snap, 80, 24)
        assert "END_OF_REASON" in seen
        assert "Owner: Firstmate · crew" in seen
        assert "Worker: task" in seen
        assert "Dependency: None recorded" in seen and "Next:" in seen
        assert "Blocker key: identity" in seen
        assert "Queue answer" not in seen

    def test_dependencies_decisions_and_technical_blocks_stay_distinct(self):
        gate, snap = self.board(bucket="gates", blocked_by="producer", title="Validation")
        assert gate.badge == "⏳ dependency" and gate.bucket == "charted"
        fields = dict(flow.UI.detail_fields(gate))
        assert fields["Dependency"].startswith("producer")
        assert "named dependency" in fields["Routing detail"]
        call, _ = self.board(bucket="decisions_open", title="Choose scope", reason="Which scope?", owner="(main)")
        assert call.badge == "⚑ pending"
        assert "authored question" in dict(flow.UI.detail_fields(call))["Routing detail"]
        blocked, snap = self.board("blocked [at=101] [key=technical]: Missing provenance; not an approval wait\n")
        assert not snap.cols["captains_call"]
        assert "approval" not in dict(flow.UI.detail_fields(blocked))["Routing detail"]
        decision, snap = self.board("needs-decision [at=101] [key=scope]: Need scope reconciliation\n")
        assert not snap.cols["captains_call"]
        assert "reconcile the declared decision" in dict(flow.UI.detail_fields(decision))["Routing detail"]

    def test_unconfirmed_native_status_does_not_invent_task_progress(self):
        for native in (None, "unknown", "garbled", "done"):
            card, _ = self.board(native=native)
            assert card.badge == "? unknown"
            assert card.activity_unconfirmed and card.blockers == []
            assert "verify task status" in dict(flow.UI.detail_fields(card))["Routing detail"]
        card, _ = self.board(native="blocked")
        assert card.badge == "⛔ Herdr blocked"
        assert dict(flow.UI.detail_fields(card))["Reason"] == "No specific reason was recorded."

    def test_details_owns_input_and_preserves_agent_open_action(self):
        card, snap = self.board("blocked [at=101]: Missing identity\n")
        ui = self.ui(snap)
        with patch.object(ui, "open_selected") as opened, patch.object(ui, "dialog_submit") as submit:
            flow.parse_input(b"d", ui)
            self.frame(ui, snap)
            flow.parse_input(b"\r omrAL\x0c", ui)
            opened.assert_not_called()
            submit.assert_not_called()
            flow.parse_input(b"\x1b", ui)
            assert ui.details is None
            flow.parse_input(b"\r", ui)
            assert ui.details is not None
            flow.parse_input(b"\x1bo", ui)
            opened.assert_called_once()

    def test_details_refreshes_after_resolution_without_stale_blocker(self):
        log = "blocked [at=101] [key=identity]: Missing provenance\n"
        card, snap = self.board(log)
        ui = self.ui(snap)
        flow.parse_input(b"d", ui)
        assert "blocked" in self.frame(ui, snap)
        card, refreshed = self.board(log + "resolved [at=102] [key=identity]: supplied\nworking [at=103]: validating\n", native="working")
        text = self.frame(ui, refreshed)
        assert "State: ● Herdr working" in text and "Blocker key:" not in text

    def test_prior_spawn_blocker_is_not_current(self):
        self.status.write_text("blocked [at=100] [key=old]: Previous run blocked\n")
        assert flow.read_open_blockers(str(self.status), 101) == []

    def test_unkeyed_blocker_resumes_with_next_phase(self):
        card, _ = self.board("blocked [at=100]: Test unavailable\nworking [at=101]: Testing\n", native="working")
        assert card.blockers == [] and card.badge == "● Herdr working"

    def test_source_truncation_is_preserved_without_inventing_missing_reason(self):
        reason = "Missing runtime-to-pane binding [truncated]"
        card, snap = self.board(f"blocked [at=101] [key=identity]: {reason}\n")
        assert dict(flow.UI.detail_fields(card))["Reason"] == reason
        fields = dict(flow.UI.detail_fields(card))
        assert "may be abbreviated" in fields["Evidence"]
        assert fields["Dependency"].startswith("None recorded")


if __name__ == "__main__":
    unittest.main()
