"""The synthetic columns communicate workflow, not supervision plumbing."""

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("flow_followup", ROOT / "scripts/flow_tui.py")
flow = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = flow
spec.loader.exec_module(flow)
DIAGNOSTIC = "harness state unavailable (unknown codex-unverified)"


class StatusFollowupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        env = patch.dict(os.environ, HERDR_PLUGIN_CONFIG_DIR=str(self.path / 'plugin-config'))
        env.start(); self.addCleanup(env.stop)
        os.environ.pop('CD_WEB_LANGUAGE', None)
        flow._LOCALIZATION = None
        self.addCleanup(setattr, flow, '_LOCALIZATION', None)
        (self.path / "state").mkdir()
        self.home = flow.Home("captain", str(self.path), "captain")
        self.status = self.path / "state/task.status"

    def card(self, log="", *, bucket="in_flight", meta=True, native="working", pane=True,
             state="unknown", doing=DIAGNOSTIC, **entry):
        self.status.write_text(log)
        if meta:
            (self.path / "state/task.meta").write_text(
                "harness=codex\nmodel=default\neffort=xhigh\nherdr_pane_id=synthetic:p1\n"
            )
        row = {"id": "task", "state": state, "doing": doing, **entry}
        with patch.object(flow, "hide_answered_default", return_value=False):
            cols, _ = flow.make_cards(
                self.home, {bucket: [row]}, flow.build_meta_index([self.home]),
                {"synthetic:p1": {"agent_status": native}} if native else {},
                {"synthetic:p1": {}} if pane else {}, [self.home],
            )
        return next(c for cards in cols.values() for c in cards)

    def render(self, card, width=40):
        return [flow._ANSI_RE.sub("", line) for line in flow.UI().render_card(card, width, False)]

    def test_synthetic_underway_keyed_report(self):
        card = self.card("working [at=1792788000]: [key=demo-component-review] Checking the selected target\n")
        assert card.badge == "● Herdr working"
        assert card.snapshot_state == "unknown" and card.activity_unconfirmed
        assert card.doing == DIAGNOSTIC
        assert flow.UI.card_status_line(card, 80) == "Report: Checking the selected target"
        for width in (24, 32, 40, 80):
            lines = self.render(card, width)
            assert len(lines) == flow.CARD_ROWS
            assert all(flow.display_width(line) <= width for line in lines)
            assert "Last report" in lines[3] and "Checking" in lines[4]
            assert "native" in lines[8]
            assert not any("[key=" in line or "unconfirmed" in line for line in lines)
        assert "[key=demo-component-review]" in card.status_text

    def test_synthetic_human_call_without_runtime(self):
        title = "Conservar 1,37 GB de datos del experimento antes de revisar"
        card = self.card(bucket="decisions_open", meta=False, native=None, pane=False,
                         summary=title, verb="captain-hold", owner="(main)")
        assert card.badge == "⚑ pending"
        assert flow.UI.card_agent_line(card) == "Human action"
        assert flow.UI.card_status_line(card, 80) == ""
        lines = self.render(card, 40)
        assert "Conservar 1,37 GB" in lines[1]
        assert "experimento" in lines[2]
        assert "no agent yet" not in "\n".join(lines)

    def test_human_action_ignores_harness_and_stats(self):
        card = self.card(bucket="decisions_open", title="Review storage", reason="Choose how to retain results")
        card.run_elapsed, card.run_tokens = "10m", "20k"
        lines = self.render(card, 80)
        assert "Waiting for a human answer" in lines[3]
        assert "Choose how to retain results" in lines[4]
        assert not any("codex" in line or "20k" in line or "10m" in line for line in lines)

    def test_real_unspawned_work_is_still_queued(self):
        card = self.card(bucket="gates", meta=False, native=None, pane=False, title="Write tests")
        assert card.bucket == "charted" and card.badge == "· queued"
        assert flow.UI.card_agent_line(card) == "no agent yet"

    def test_native_busy_cannot_promote_a_queued_task(self):
        card = self.card(bucket="gates", title="Write tests")
        assert card.bucket == "charted" and card.badge == "· queued"

    def test_select_report_before_control_record_but_keep_raw_inspection(self):
        control = "working [at=1792788100]: [key=delivery] Inbox 009 acknowledged; root routing complete"
        card = self.card("working [at=1792788000]: Tests now cover retry failures\n" + control + "\n")
        assert flow.UI.card_status_line(card, 80) == "Report: Tests now cover retry failures"
        assert card.report_at == 1792788000
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            flow._print_home_cols("captain", str(self.path), {"underway": [card]})
        assert "[key=delivery]" in output.getvalue()
        assert "[at=1792788000]" in output.getvalue()
        assert self.status.read_text().endswith(control + "\n")

    def test_structural_messages_are_rejected_as_a_whole(self):
        for text in (
            "[key=receipt] Inbox 009 aplicado/acusado y reporte actualizado",
            "resolve-key old-call delivered to root",
            "root routing to /root/secondmate/task",
            "firstmate -> root: done", "Endpoint endpoint_task_id=task ready",
            "routed to root for acknowledgement", "forwarded to firstmate",
            "pane wN:p1 done", "task [key=internal] complete",
            "harness state unavailable (unknown stale-event)",
        ):
            with self.subTest(text=text):
                card = self.card(f"working [at=1792788100]: {text}\n")
                assert flow.UI.card_status_line(card, 80) == "No task report"

    def test_resolution_does_not_resurrect_older_waiting_report(self):
        card = self.card("paused [at=1792788000]: Waiting for approval\n"
                         "resolved [key=approval] [at=1792788100]: answered: continue\n", native=None)
        assert card.badge == "? unknown"
        assert flow.UI.card_status_line(card, 80) == "No task report"

    def test_metadata_on_either_side_of_colon_and_timestamp_order(self):
        for line in (
            "paused [at=1792788000] [key=review]: Waiting for test results",
            "paused [key=review] [at=1792788000]: Waiting for test results",
            "paused [at=1792788000]: [key=review] Waiting for test results",
        ):
            with self.subTest(line=line):
                card = self.card(line + "\n")
                assert card.badge == "⏸ paused · reported"
                assert card.report_at == 1792788000
                assert flow.UI.card_status_line(card, 80) == "Report: Waiting for test results"

    def test_explicit_outcomes_survive_busy_native_without_claiming_current_confirmation(self):
        for state in ("paused", "parked", "blocked", "failed", "done", "needs-decision"):
            with self.subTest(state=state):
                card = self.card(f"{state} [at=1792788000] [key=review]: Review the result\n")
                assert "reported" in card.badge and "Herdr" not in card.badge
                assert card.bucket == "underway"
                prefix = "Reason" if state in ("blocked", "needs-decision") else "Report"
                assert flow.UI.card_status_line(card, 80) == f"{prefix}: Review the result"

    def test_missing_stale_conflicting_and_malformed_sources(self):
        for native, pane, state, doing, badge in (
            (None, True, "unknown", DIAGNOSTIC, "? unknown"),
            ("working", False, "unknown", DIAGNOSTIC, "? unknown"),
            ("done", True, "unknown", DIAGNOSTIC, "? unknown"),
            ("garbled", True, "unknown", DIAGNOSTIC, "? unknown"),
            ("working", True, "working", DIAGNOSTIC, "● Herdr working"),
            ("idle", True, "unknown", "harness state unavailable (unknown stale-event)", "○ Herdr idle"),
        ):
            with self.subTest(native=native, pane=pane, state=state):
                card = self.card("not a status record\n", native=native, pane=pane, state=state, doing=doing)
                assert card.badge == badge
                assert card.activity_unconfirmed and card.bucket == "underway"
                assert flow.UI.card_status_line(card, 80) == "No task report"

    def test_conflicting_done_diagnostic_cannot_claim_merge_readiness(self):
        card = self.card(state="done", doing=DIAGNOSTIC)
        assert card.bucket == "underway"
        assert card.badge == "✓ done · reported"
        assert card.activity_unconfirmed

    def test_native_blocker_names_its_source_when_firstmate_reports_working(self):
        card = self.card(state="working", doing="harness busy (claude-hook)", native="blocked")
        assert card.badge == "⛔ Herdr blocked"
        assert card.snapshot_state == "working"

    def test_human_call_does_not_collect_agent_run_stats(self):
        card = self.card(bucket="decisions_open", title="Review storage")
        with patch.object(flow, "agent_run_stats", side_effect=AssertionError("human call has no run")):
            flow.enrich_cards_run_stats({"captains_call": [card]}, {})
        assert card.run_elapsed == card.run_tokens == ""

    def test_a_prior_spawn_outcome_is_historical(self):
        card = self.card("paused [at=1792788000]: Waiting for review\n", native=None)
        meta = self.path / "state/task.meta"
        meta.write_text(meta.read_text() + "spawn_gen=s1792788100.1\n")
        with patch.object(flow, "read_meta", return_value={"spawn_gen": "s1792788100.1"}):
            card = self.card("paused [at=1792788000]: Waiting for review\n", native=None)
        assert card.badge == "? unknown"
        assert flow.UI.card_status_line(card, 80) == "Report: Waiting for review"


if __name__ == "__main__":
    unittest.main()
