"""Event provenance, clock boundaries and real card/detail rendering."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("flow_card_time", ROOT / "scripts/flow_tui.py")
flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)
NOW = 1792843200  # frozen UTC instant, unrelated to file mtimes or poll time


class CardTimeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "state").mkdir()
        self.status = self.root / "state/task.status"
        self.home = flow.Home("fixture", str(self.root), "captain")
        env = patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": str(self.root / "plugin"),
                                    "HERDR_PLUGIN_CONFIG_DIR": str(self.root / "plugin-config"),
                                    "FM_FLOW_SHOW_LANDED": "1"})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('CD_WEB_LANGUAGE', None)
        flow._LOCALIZATION = None
        self.addCleanup(setattr, flow, '_LOCALIZATION', None)

    def board(self, bucket="in_flight", log="", **fields):
        self.status.write_text(log)
        row = {"id": "task", "name": "Inspect clock", "title": "Inspect clock",
               "what": "Inspect clock", "state": "working", "owner": "(main)", **fields}
        snap = flow.Snapshot()
        snap.home, snap.homes = self.home, [self.home]
        with patch.object(flow, "hide_answered_default", return_value=False):
            snap.cols, snap.totals = flow.make_cards(self.home, {bucket: [row]},
                flow.build_meta_index([self.home]), {}, {}, [self.home])
        card = next(c for cards in snap.cols.values() for c in cards)
        return card, snap

    def test_relative_boundaries_and_old_absolute_fallback(self):
        for age, expected in ((0, "just now"), (59, "just now"), (60, "1 minute ago"),
                              (3599, "59 minutes ago"), (3600, "1 hour ago"),
                              (86399, "23 hours ago"), (86400, "1 day ago"),
                              (604799, "6 days ago"), (604800, "1 week ago"),
                              (4 * 604800, "4 weeks ago")):
            with self.subTest(age=age):
                event = flow.CardTime("Updated", NOW - age, "test declaration")
                self.assertEqual(flow.card_time_line(event, 80, NOW), "Updated " + expected)
        event = flow.CardTime("Updated", NOW - 120)
        self.assertEqual(flow.card_time_line(event, 19, NOW), "Updated 2m ago")
        old = flow.CardTime("Updated", NOW - 366 * 86400)
        self.assertRegex(flow.card_time_line(old, 80, NOW), r"Updated \d{4}-\d{2}-\d{2}$")

    def test_missing_invalid_naive_and_future_are_not_recent_events(self):
        for value in ("garbage", "2026-02-30", "9999-12-31T23:59:59-07:00",
                      -1, 0, True, float("nan"), float("inf"), 10**30, {}):
            event = flow.CardTime("Updated", value)
            self.assertIsNone(event.epoch)
            self.assertIn("unavailable", flow.card_time_line(event, 80, NOW))
            self.assertIn("invalid timestamp", flow.card_time_detail(event))
        naive = flow.CardTime("Updated", "2026-10-24T01:30:00")
        self.assertIn("timezone missing", flow.card_time_detail(naive))
        minute = flow.CardTime("Updated", "2026-10-24T01:30+00:00")
        self.assertIsNone(minute.epoch)
        self.assertIn("seconds not recorded", flow.card_time_detail(minute))
        self.assertIn("unknown", flow.card_time_line(flow.CardTime("Asked"), 80, NOW))
        future = flow.CardTime("Updated", NOW + 1)
        self.assertIn("future", flow.card_time_line(future, 19, NOW))
        with patch.object(flow.time, "time", return_value=NOW):
            self.assertIn("check producer/terminal clock", flow.card_time_detail(future))

    def test_filed_date_never_acquires_midnight_or_an_invented_age(self):
        card, _ = self.board("gates", filed="2026-10-23", generated="2026-10-24T12:00:00Z")
        self.assertIsNone(card.event_time.epoch)
        self.assertIn("Filed 2026-10-23 (date only)", flow.card_time_line(card.event_time, 80, NOW))
        exact = dict(flow.UI.detail_fields(card))["Time"]
        self.assertIn("time and timezone not recorded", exact)
        self.assertIn("bearings.gates.filed", exact)
        self.assertNotIn("00:00", exact)
        card, _ = self.board("gates")
        self.assertIsNone(card.event_time.day)  # neither mtime nor first seen

    def test_question_creation_does_not_use_task_or_generated_time(self):
        card, _ = self.board("decisions_open", filed="2026-10-23", generated="2026-10-24T12:00:00Z")
        self.assertEqual(flow.card_time_line(card.event_time, 80, NOW), "Asked unknown")
        self.assertIn("Current question/hold creation is not exported", dict(flow.UI.detail_fields(card))["Time"])

    def test_last_meaningful_declaration_survives_poll_and_receipt(self):
        meaningful = f"working [at={NOW - 120}]: Editing the date renderer\n"
        noise = (f"working [at={NOW - 60}]: harness busy (codex-hook)\n"
                 f"working [at={NOW - 30}]: heartbeat poll\n"
                 f"working [at={NOW - 10}]: inbox acknowledged\n")
        card, snap = self.board(log=meaningful + noise)
        event_epoch = card.event_time.epoch
        self.assertIn("can’t tell which run this is", flow.card_time_detail(card.event_time))
        ui = flow.UI()
        with patch.object(flow.time, "time", return_value=NOW):
            first = "\n".join(ui.render_card(card, 40, True, snap))
        with patch.object(flow.time, "time", return_value=NOW + 60):
            second = "\n".join(ui.render_card(card, 40, True, snap))
        self.assertIn("2 min ago", first)
        self.assertIn("3 min ago", second)
        self.assertEqual(card.event_time.epoch, event_epoch)
        # A fresh collector build also keeps the source instant, not poll time.
        card2, _ = self.board(log=meaningful + noise)
        self.assertEqual(card2.event_time.epoch, NOW - 120)
        ui.collector.snapshot = lambda: snap
        ui.col_idx = 1
        frames = []
        for clock in (NOW, NOW + 60):
            with patch.object(flow.shutil, "get_terminal_size", return_value=os.terminal_size((180, 24))), \
                    patch.object(flow.time, "time", return_value=clock), \
                    patch.object(ui, "paint", side_effect=lambda lines, *_: frames.append("\n".join(lines))):
                ui.render(snap, force=True)
        self.assertIn("2 min ago", frames[0])
        self.assertIn("3 min ago", frames[1])
        with patch.object(flow.time, "time", return_value=NOW):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                flow._print_home_cols("fixture", str(self.root), snap.cols)
        self.assertIn("latest task update", out.getvalue())

    def test_unstamped_newer_report_and_previous_spawn_do_not_borrow_precision(self):
        log = f"working [at={NOW - 120}]: Editing renderer\nworking: Checking boundaries\n"
        card, _ = self.board(log=log)
        self.assertIsNone(card.event_time.epoch)
        (self.root / "state/task.meta").write_text(f"spawn_gen={NOW}-123\n")
        self.assertEqual(flow.task_update_time(str(self.status), NOW), ("working", 0))
        self.status.write_text(f"working [at={NOW - 120}]: Previous worker report\n")
        self.assertEqual(flow.task_update_time(str(self.status), NOW), ("", 0))

    def test_unresolved_owner_cannot_borrow_local_same_named_task_time(self):
        card, _ = self.board(id="unknown-mate/task", log=f"working [at={NOW - 120}]: Editing local task\n")
        self.assertIsNone(card.event_time.epoch)
        self.assertIn("Remote or unknown crew", flow.card_time_detail(card.event_time))

    def test_blocker_and_resolution_are_task_events_but_keep_their_status(self):
        log = f"blocked [key=dependency] [at={NOW - 120}]: inbox result missing authenticated binding\n"
        card, _ = self.board(log=log)
        self.assertIn("blocked", card.badge)
        self.assertEqual(card.event_time.epoch, NOW - 120)
        log += f"resolved [key=dependency] [at={NOW - 60}]: Dependency supplied\n"
        card, _ = self.board(log=log)
        self.assertFalse(card.blockers)
        self.assertEqual(card.event_time.epoch, NOW - 60)

    def test_done_report_is_readiness_evidence_not_delivery(self):
        card, _ = self.board(state="done", log=f"done [at={NOW - 3600}]: Candidate ready for review\n")
        self.assertEqual(card.bucket, "underway")
        self.assertEqual(flow.card_time_line(card.event_time, 80, NOW), "Done report 1 hour ago")
        self.assertIn("not delivery or review time", dict(flow.UI.detail_fields(card))["Time"])
        card, _ = self.board(state="done", log=f"working [at={NOW - 10}]: Revising candidate\n")
        self.assertIsNone(card.event_time.epoch)
        landed, _ = self.board("landed", log=f"done [at={NOW - 3600}]: Candidate ready\n")
        self.assertEqual(flow.card_time_line(landed.event_time, 80, NOW), "Delivered unknown")
        text = flow.delivery_content({"id": "task", "closed": "2026-10-23", "source": "archive"}, self.home)
        self.assertIn("2026-10-23 (date only; time and timezone not recorded)", text)

    @unittest.skipUnless(hasattr(time, "tzset"), "requires terminal timezone support")
    def test_exact_offset_dst_fold_and_calendar_dates(self):
        try:
            # Specify the fold rules directly: future regional tzdata changes
            # must not turn this conversion test into a policy prediction.
            with patch.dict(os.environ, {"TZ": "MST7MDT,M3.2.0,M11.1.0"}):
                time.tzset()
                early = flow.CardTime("Updated", "2026-11-01T01:30:00-06:00", "worker status")
                late = flow.CardTime("Updated", "2026-11-01T01:30:00-07:00", "worker status")
                self.assertEqual(late.epoch - early.epoch, 3600)
                self.assertIn("01:30:00-06:00", flow.card_time_detail(early))
                self.assertIn("01:30:00-07:00", flow.card_time_detail(late))
                self.assertIn("08:30:00+00:00", flow.card_time_detail(late))
                self.assertIn("2026-11-01", flow.card_time_detail(flow.CardTime("Filed", "2026-11-01")))
        finally:
            time.tzset()

    def test_producer_kind_column_and_precision_mapping(self):
        cases = (("gates", "working", "filed", "Filed"),
                 ("gates", "working", "queued", "Queued"),
                 ("gates", "working", "asked", "Asked"),
                 ("decisions_open", "working", "asked", "Asked"),
                 ("in_flight", "working", "updated", "Updated"),
                 ("in_flight", "done", "ready", "Ready"),
                 ("in_flight", "done", "done_report", "Done report"),
                 ("landed", "done", "delivered", "Delivered"))
        source = "  backlog task:17 / current hold · producer v1  "
        for bucket, state, kind, label in cases:
            for precision, value in (("date", "2026-10-23"),
                                     ("second", "2026-10-24T12:00:00-06:00")):
                with self.subTest(kind=kind, precision=precision):
                    raw = dict(kind=kind, value=value, precision=precision, source=source)
                    card, _ = self.board(bucket, state=state, card_time=raw)
                    self.assertEqual(card.event_time.label, label)
                    self.assertEqual(card.event_time.source, source)
                    self.assertEqual(card.event_time.producer_kind, kind)
                    exact = flow.card_time_detail(card.event_time)
                    self.assertIn("Source: " + source + ".", exact)
                    self.assertIn("precision: " + precision, exact)
                    self.assertNotIn("Event ID:", exact)
                    if precision == "date":
                        self.assertIsNone(card.event_time.epoch)
                        self.assertIn("time and timezone not recorded", exact)
                    else:
                        self.assertIsNotNone(card.event_time.epoch)
                        self.assertIn("(UTC)", exact)

    def test_producer_wrong_column_never_relabels_filing_done_or_question(self):
        cases = (("gates", "working", "delivered", "Filed"),
                 ("gates", "working", "updated", "Filed"),
                 ("decisions_open", "working", "filed", "Asked"),
                 ("in_flight", "working", "ready", "Updated"),
                 ("in_flight", "done", "delivered", "Done report"),
                 ("landed", "done", "done_report", "Delivered"))
        for bucket, state, kind, fallback_label in cases:
            with self.subTest(bucket=bucket, kind=kind):
                raw = dict(kind=kind, value="2026-10-24T12:00:00Z", precision="second", source="producer")
                card, _ = self.board(bucket, state=state, card_time=raw)
                self.assertEqual(card.event_time.label, fallback_label)
                self.assertIsNone(card.event_time.epoch)
                self.assertIn("event kind does not match", flow.card_time_detail(card.event_time))

    def test_time_sources_translate_only_board_wording(self):
        # "Recap" is a catalog entry; a producer that writes it keeps its own word.
        self.addCleanup(setattr, flow, "_LOCALIZATION", None)
        with patch.dict(os.environ, CD_WEB_LANGUAGE="es"):
            flow._LOCALIZATION = None
            produced, _ = self.board(card_time=dict(kind="updated", value="2026-10-24T12:00:00Z",
                                                    precision="second", source="Recap"))
            board, _ = self.board(log=f"working [at={NOW - 120}]: Editing renderer\n")
            produced_detail, board_detail = (flow.card_time_detail(c.event_time) for c in (produced, board))
        self.assertIn("Recap.", produced_detail)
        self.assertNotIn("Resumen", produced_detail)
        self.assertIn("última actualización de la tarea", board_detail)
        self.assertIn("task.status: latest task update", board.event_time.source)

    def test_producer_invalid_contract_retains_honest_source_and_age(self):
        good = dict(kind="updated", value="2026-10-24T12:00:00Z", precision="second", source="authored event")
        invalid = [[], "bad", {}, {**good, "kind": []}, {**good, "precision": "minute"},
                   {**good, "value": "2026-10-24T12:00:00"},
                   {**good, "value": "2026-10-24T12:00Z"},
                   {**good, "value": "2026-10-24"},
                   {**good, "precision": "date"},
                   {**good, "value": "2026-02-30T12:00:00Z"},
                   {**good, "value": 1792843200}, {**good, "value": None},
                   {**good, "source": ""}, {**good, "source": "  "},
                   {**good, "source": None}, {**good, "source": {"file": "task.md"}},
                   {**good, "source": "bad\x1b[31msource"}, {**good, "source": "source\nsecond row"},
                   {**good, "event_id": ""}, {**good, "event_id": 12},
                   {**good, "event_id": "event\x00id"}]
        for raw in invalid:
            with self.subTest(raw=raw):
                card, _ = self.board(card_time=raw, log=f"working [at={NOW - 120}]: Editing renderer\n")
                self.assertEqual(card.event_time.epoch, NOW - 120)
                self.assertEqual(card.event_time.label, "Updated")
                self.assertIn("task.status:", card.event_time.source)
                exact = flow.card_time_detail(card.event_time)
                self.assertIn("Producer card_time ignored:", exact)
                self.assertIn("fallback shown", exact)
                self.assertNotIn("\x1b", exact)
                self.assertEqual(flow.card_time_line(card.event_time, 80, NOW), "Updated 2 minutes ago")

    def test_producer_null_and_missing_leave_fallback_unchanged(self):
        log = f"working [at={NOW - 120}]: Editing renderer\n"
        original, _ = self.board(log=log)
        missing, _ = self.board(log=log, card_time=None)
        self.assertEqual(flow.card_time_detail(original.event_time), flow.card_time_detail(missing.event_time))
        self.assertFalse(missing.event_time.producer_kind)
        self.assertFalse(missing.event_time.rejected_producer)

    def test_producer_remote_event_identity_and_clock_refresh(self):
        raw = dict(kind="asked", value="2026-10-24T12:00:00Z", precision="second",
                   source="remote current hold ledger: question 2", event_id="question:2")
        card, snap = self.board("decisions_open", id="unresolved/task", card_time=raw)
        epoch = card.event_time.epoch
        self.assertIsNotNone(epoch)
        self.assertEqual(card.event_time.event_id, "question:2")
        self.assertIn("Event ID: question:2", dict(flow.UI.detail_fields(card))["Time"])
        ui = flow.UI()
        for clock, expected in ((epoch + 120, "2 min"), (epoch + 180, "3 min"),
                                (epoch - 1, "future date")):
            with patch.object(flow.time, "time", return_value=clock):
                lines = [flow._ANSI_RE.sub("", s) for s in ui.render_card(card, 24, True, snap)]
            self.assertIn(expected, lines[9])
            self.assertTrue(all(flow.display_width(s) <= 24 for s in lines))
        self.assertEqual(card.event_time.epoch, epoch)
        repeated, _ = self.board("decisions_open", id="unresolved/task", card_time={**raw, "event_id": None})
        self.assertEqual(repeated.event_time.epoch, epoch)
        self.assertNotIn("Event ID:", flow.card_time_detail(repeated.event_time))

    def test_producer_readiness_and_delivery_keep_existing_status_and_reader(self):
        raw = dict(kind="ready", value="2026-10-24T12:00:00Z", precision="second", source="ready ledger")
        card, _ = self.board(state="done", kind="secondmate", card_time=raw,
                             log=f"blocked [key=owner] [at={NOW - 120}]: Waiting for owner\n")
        self.assertEqual(card.bucket, "underway")
        self.assertIn("blocked", card.badge)
        self.assertEqual(card.event_time.label, "Ready")
        delivered, _ = self.board("landed", card_time={**raw, "kind": "delivered", "source": "delivery receipt:17"})
        text = flow.delivery_content({"id": "task", "closed": "2026-10-24", "source": "archive"},
                                     self.home, delivered.event_time)
        self.assertIn("Board time: Delivered:", text)
        self.assertIn("Source: delivery receipt:17.", text)
        self.assertIn("Producer event: delivered; precision: second", text)

    def test_final_exporter_attempt_context_preserves_bounds_without_event_identity(self):
        raw = dict(kind="updated", value="2026-10-24T12:00:00-06:00", precision="second",
                   source="authored status [at]", original_value="1792864800",
                   attempt_boundary="unknown", current_attempt="legacy-gen")
        card, _ = self.board(card_time=raw)
        detail = flow.card_time_detail(card.event_time)
        self.assertIn("Exported value: 2026-10-24T12:00:00-06:00", detail)
        self.assertIn("Original value: 1792864800", detail)
        self.assertIn("Attempt boundary: unknown", detail)
        self.assertIn("Current attempt (context only): legacy-gen", detail)
        self.assertNotIn("Event ID:", detail)
        known, _ = self.board(card_time={**raw, "attempt_boundary": "2026-10-24T11:00:00-06:00",
                                        "attempt": "bounded-gen", "current_attempt": None})
        self.assertEqual(known.event_time.epoch, card.event_time.epoch)
        detail = flow.card_time_detail(known.event_time)
        self.assertIn("Attempt: bounded-gen", detail)
        self.assertNotIn("Current attempt (context only):", detail)
        invalid, _ = self.board(card_time={**raw, "original_value": 1792864800,
                                          "current_attempt": {"bad": "identity"}})
        self.assertEqual(invalid.event_time.epoch, card.event_time.epoch)
        detail = flow.card_time_detail(invalid.event_time)
        self.assertIn("Original value: 1792864800", detail)
        self.assertIn("Ignored invalid producer context: current_attempt", detail)

    def test_minimal_columns_preserve_advancing_age_and_report_semantics(self):
        for kind, label, short in (("filed", "Filed", "Fld"), ("queued", "Queued", "Que"), ("asked", "Asked", "Ask"),
                                   ("updated", "Updated", "Upd"), ("done_report", "Done report", "Rpt"),
                                   ("ready", "Ready", "Rdy"), ("delivered", "Delivered", "Del")):
            with self.subTest(kind=kind):
                event = flow.CardTime(label, NOW - 120, "fixture")
                self.assertEqual(flow.card_time_line(event, 6, NOW), short + " 2m")
                self.assertEqual(flow.card_time_line(event, 6, NOW + 60), short + " 3m")
        self.assertEqual(flow.card_time_line(flow.CardTime("Updated", NOW), 6, NOW), "U now")
        self.assertEqual(flow.card_time_line(flow.CardTime("Updated", NOW + 1), 6, NOW), "U fut")

    def test_minimal_calendar_and_missing_values_stay_visible_for_all_event_kinds(self):
        for label, short, tag in (("Filed", "Fld", "F"), ("Queued", "Que", "Q"), ("Asked", "Ask", "A"),
                                  ("Updated", "Upd", "U"), ("Ready", "Rdy", "R"),
                                  ("Done report", "Rpt", "D"), ("Delivered", "Del", "D")):
            with self.subTest(label=label):
                event = flow.CardTime(label, "2026-10-21", "original calendar date")
                self.assertEqual(flow.card_time_line(event, 6, NOW), tag + "21Oct")
                self.assertEqual(flow.card_time_line(event, 7, NOW), tag + " 21Oct")
                for width in (6, 7, 10, 19, 80):
                    text = flow.card_time_line(event, width, NOW)
                    self.assertLessEqual(flow.display_width(text), width)
                    self.assertTrue("21Oct" in text or "2026-10-21" in text)
                self.assertIsNone(event.epoch)
                self.assertIn("2026-10-21 (date only; time and timezone not recorded)", flow.card_time_detail(event))
                self.assertEqual(flow.card_time_line(flow.CardTime(label), 6, NOW), short + " ?")
                self.assertEqual(flow.card_time_line(flow.CardTime(label, "invalid"), 6, NOW), short + " !")
        old = flow.CardTime("Updated", "2025-10-01T12:00:00Z")
        original = old.epoch
        self.assertRegex(flow.card_time_line(old, 6, NOW), r"U\d{2}[A-Z][a-z]{2}$")
        self.assertEqual(old.epoch, original)
        card, snap = self.board("landed")
        row = flow.UI().render_card(card, 11, False, snap)[8]
        self.assertIn("?", flow._ANSI_RE.sub("", row))
        self.assertNotIn("Deliv…", row)

    def test_narrow_card_details_and_scroll_keep_status_owner_and_geometry(self):
        card, snap = self.board(state="paused", log=f"paused [at={NOW - 120}]: Waiting for local validation\n")
        card.worktree = "/tmp/fixture/task"
        ui = flow.UI()
        ui.collector.snapshot = lambda: snap
        ui.col_idx = 1
        for width in (24, 32, 40, 80):
            with patch.object(flow.time, "time", return_value=NOW):
                lines = [flow._ANSI_RE.sub("", s) for s in ui.render_card(card, width, True, snap)]
            self.assertEqual(len(lines), flow.CARD_ROWS)
            self.assertTrue(all(flow.display_width(s) <= width for s in lines))
            self.assertIn("On hold", lines[3])
            self.assertIn("2 min", lines[9])
            self.assertIn("Enter:", lines[10])
        flow.parse_input(b"d", ui)
        seen = ""
        for _ in range(30):
            frames = []
            with patch.object(flow.shutil, "get_terminal_size", return_value=os.terminal_size((80, 24))), \
                    patch.object(flow.time, "time", return_value=NOW), \
                    patch.object(ui, "paint", side_effect=lambda lines, *_: frames.append(lines)):
                ui.render(snap, force=True)
            self.assertEqual(len(frames[0]), 24)
            self.assertTrue(all(flow.ansi_width(s) <= 80 for s in frames[0]))
            seen += "\n".join(flow._ANSI_RE.sub("", s) for s in frames[0])
            if ui.details_scroll >= ui.details_max:
                break
            flow.parse_input(b"\x1b[6~", ui)
        self.assertIn("(terminal local)", seen)
        self.assertIn("(UTC)", seen)
        self.assertIn("Source:", seen)
        self.assertIn("Crew: Firstmate", seen)
        flow.parse_input(b"\x1b", ui)
        self.assertIsNone(ui.details)


if __name__ == "__main__":
    unittest.main()
