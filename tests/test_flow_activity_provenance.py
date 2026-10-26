"""Runtime confirmation, native activity and declared progress stay distinct."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("flow_activity", ROOT / "scripts/flow_tui.py")
flow = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = flow
spec.loader.exec_module(flow)

DIAGNOSTIC = "harness state unavailable (unknown codex-unverified)"


def card_for(tmp_path, *, state="unknown", doing=DIAGNOSTIC,
             declared="working", text="editing the renderer", native="working", harness="codex"):
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state/task.meta").write_text(
        f"harness={harness}\nherdr_pane_id=synthetic:p1\n"
    )
    if declared is not None:
        (tmp_path / "state/task.status").write_text(
            f"{declared} [at=1792785000]: {text}\n"
        )
    home = flow.Home("captain", str(tmp_path), "captain")
    entry = {"id": "task", "state": state, "doing": doing, "name": "Status correction"}
    agents = {"synthetic:p1": {"agent_status": native}} if native else {}
    cols, _ = flow.make_cards(
        home, {"in_flight": [entry]}, flow.build_meta_index([home]),
        agents, {"synthetic:p1": {}}, [home],
    )
    return next(c for cards in cols.values() for c in cards), cols


class ActivityProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)

    def card(self, **kwargs):
        return card_for(self.path, **kwargs)

    def test_mixed_source_card(self):
        card, _ = self.card()
        assert card.bucket == "underway"
        assert card.badge == "● Herdr working"
        assert flow.UI.card_status_line(card, 80) == "Report: editing the renderer"
        assert card.snapshot_state == "unknown"
        assert card.live_status == "working"
        assert card.doing == DIAGNOSTIC

    def test_unavailable_confirmation_cannot_become_shipping_or_idle(self):
        for native in (None, "idle", "done", "working", "blocked"):
            for state, doing in (
                ("unknown", DIAGNOSTIC), ("", ""),
                ("unknown", "no current-state source available"),
                ("unknown", "harness state unavailable (unknown stale-event)"),
                ("unknown", "harness state unavailable (unknown malformed)"),
                ("working", DIAGNOSTIC),
            ):
                with self.subTest(native=native, state=state, doing=doing):
                    card, _ = self.card(state=state, doing=doing, native=native)
                    assert card.badge == {"working": "● Herdr working", "idle": "○ Herdr idle", "blocked": "⛔ Herdr blocked"}.get(native, "? unknown")
                    assert "shipping" not in card.badge
                    if native in ("working", "idle", "blocked"):
                        assert "Herdr" in card.badge
                    else:
                        assert "Herdr" not in card.badge
                    assert flow.UI.card_status_line(card, 80).startswith("Report: ")

    def test_unavailable_confirmation_without_a_declaration(self):
        card, _ = self.card(declared=None, native=None)
        assert card.badge == "? unknown"
        assert flow.UI.card_status_line(card, 80) == "No task report"

    def test_known_firstmate_activity_keeps_shipping(self):
        for native in (None, "working", "idle"):
            with self.subTest(native=native):
                card, _ = self.card(state="working", doing="harness busy (claude-hook)",
                                    native=native, harness="claude")
                assert card.badge == "● shipping"
                assert not card.activity_unconfirmed

    def test_firstmate_task_outcomes_survive_native_working(self):
        for state, label in (
            ("paused", "paused"), ("parked", "parked"), ("blocked", "blocked"),
            ("failed", "failed"), ("done", "done"),
        ):
            with self.subTest(state=state):
                card, cols = self.card(state=state, doing="waiting for tests")
                assert flow.badge_label_from_badge(card.badge) == label
                assert card in cols["underway"]
                assert flow.UI.card_status_line(card, 80) == "waiting for tests"

    def test_declared_outcomes_survive_unknown_runtime_without_claiming_confirmation(self):
        for declared, label in (
            ("paused", "paused"), ("parked", "parked"), ("blocked", "blocked"),
            ("failed", "failed"), ("needs-decision", "decision"), ("done", "done"),
        ):
            with self.subTest(declared=declared):
                card, _ = self.card(declared=declared, text="waiting for review")
                assert label in card.badge
                assert "reported" in card.badge
                assert "shipping" not in card.badge
                # A log declaration cannot prove merge readiness.
                assert card.bucket == "underway"
                prefix = "Reason" if declared in ("blocked", "needs-decision") else "Report"
                assert flow.UI.card_status_line(card, 80) == f"{prefix}: waiting for review"

    def test_confirmed_runtime_does_not_reuse_a_superseded_pause(self):
        card, _ = self.card(state="working", doing="validating (run active)", declared="paused")
        assert card.badge == "◐ validating"

    def test_actionable_detail_and_stats_are_preserved(self):
        card, _ = self.card(state="blocked", doing="credentials expired")
        assert flow.UI.card_status_line(card, 80) == "credentials expired"
        card, _ = self.card()
        card.run_elapsed, card.run_tokens = "3m 2s", "12k"
        assert flow.UI.card_status_line(card, 80) == "Report: editing the renderer · 3m 2s · ↓ 12k tokens"

    def test_inspection_retains_runtime_diagnostic_and_all_sources(self):
        card, cols = self.card()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            flow._print_home_cols("captain", str(self.path), cols)
        text = output.getvalue()
        assert f"Firstmate: unknown; {DIAGNOSTIC}" in text
        assert "Herdr: working" in text
        assert "Reported: working; editing the renderer" in text
        assert card.doing == DIAGNOSTIC

    def test_card_keeps_uncertainty_and_reported_prefix_with_time_row(self):
        card, _ = self.card()
        for width in (24, 32, 40, 80):
            with self.subTest(width=width):
                lines = flow.UI().render_card(card, width, False)
                plain = [flow._ANSI_RE.sub("", line) for line in lines]
                assert len(lines) == flow.CARD_ROWS
                assert all(flow.display_width(line) <= width for line in plain)
                assert "Último reporte" in plain[3]
                assert "nativo" in plain[8]
                assert "editing" in plain[4]
                assert "harness state unavailable" not in "\n".join(plain)


if __name__ == "__main__":
    unittest.main()
