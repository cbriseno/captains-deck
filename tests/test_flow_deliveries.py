"""Delivery boundaries, canonical archive reads and real UI input, in isolation."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('flow_deliveries', ROOT / 'scripts/flow_tui.py')
flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)


def fixture(root):
    root = Path(root)
    (root / 'data/old').mkdir(parents=True)
    (root / 'state').mkdir()
    (root / '.tasks.toml').write_text('backend = "markdown"\n[markdown]\npath = "data/backlog.md"\narchive = "data/done-archive.md"\ndone_keep = 10\n')
    (root / 'data/backlog.md').write_text('## Done\n- [x] recent - SCOUT Completed study data/recent/report.md (reported 2026-10-23)\n  Checks: targeted pass; wider validation pending.\n')
    (root / 'data/done-archive.md').write_text('\n## Archived 2026-10-22\n- [x] old - Author installed skill; evals pending - local main (done 2026-10-21)\n  Result: installed, no evals run.\n' + ''.join(f'- [x] history-{n:02} - Historical delivery {n} - local main (done 2026-09-01)\n' for n in range(30)))
    (root / 'data/old/delivery.md').write_text('# Scope\nOnly the skill authoring subtask finished.\n\n## Result\nInstalled skill.\n## Checks\nTargeted pass.\n## Pending\nEvals not run.\n[Report](report.md)\n[Receipt](receipt.json)\n')
    (root / 'data/old/report.md').write_text('# Full report\n' + 'Full available text with Unicode á and 漢字.\n' * 25 + '\nEND_OF_REPORT\n')
    (root / 'data/old/receipt.json').write_text('{"checks":"targeted pass", "pending":"evals", "marker":"RECEIPT_END"}')
    return flow.Home('fixture', str(root), 'captain')


def snapshot(home):
    snap = flow.Snapshot()
    snap.home, snap.homes = home, [home]
    snap.cols, snap.totals = flow.make_cards(home, {'landed': [{'id': 'old', 'what': 'truncated title…', 'owner': '(main)', 'artifact': '-'}]}, {}, {}, {}, [home])
    return snap


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = fixture(self.root)
        self.env = patch.dict(os.environ, {'HERDR_PLUGIN_STATE_DIR': str(self.root / 'plugin-state'),
                                          'HERDR_PLUGIN_CONFIG_DIR': str(self.root / 'plugin-config'), 'FM_FLOW_SHOW_LANDED': '1'})
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop('CD_WEB_LANGUAGE', None)
        flow._LOCALIZATION = None
        self.addCleanup(setattr, flow, '_LOCALIZATION', None)

    def ui(self):
        snap = snapshot(self.home)
        ui = flow.UI()
        ui.collector.snapshot = lambda: snap
        ui.col_idx = next(i for i, (key, _) in enumerate(ui.columns) if key == 'landed')
        return ui, snap

    def frame(self, ui, snap, width=130, height=15):
        frames = []
        with patch.object(flow.shutil, 'get_terminal_size', return_value=os.terminal_size((width, height))), patch.object(ui, 'paint', side_effect=lambda lines, *_: frames.append(lines)):
            ui.render(snap, force=True)
        lines = [flow._ANSI_RE.sub('', x) for x in frames[0]]
        self.assertEqual(len(lines), height)
        self.assertTrue(all(flow.display_width(line) <= width for line in lines))
        return '\n'.join(lines)

    def test_archive_uses_canonical_grammar_beyond_recent_limits_without_writes(self):
        before = {p: p.read_bytes() for p in (self.root / '.tasks.toml', self.root / 'data/backlog.md', self.root / 'data/done-archive.md')}
        rows, note, complete = flow.delivery_records(self.home)
        self.assertTrue(complete)
        self.assertEqual(len(rows), 32)
        old = next(r for r in rows if r['id'] == 'old')
        self.assertEqual((old['source'], old['closed'], old['verb']), ('archive', '2026-10-21', 'done'))
        self.assertIn('done-archive.md', note)
        self.assertIn('Shows what Firstmate still keeps', note)
        self.assertTrue(all(path.read_bytes() == data for path, data in before.items()))

    def test_reopened_current_row_wins_and_captain_questions_are_not_deliveries(self):
        path = self.root / 'data/backlog.md'
        with path.open('a') as out:
            out.write('- [x] question - Chose scope (kind: captain) (done 2026-10-23)\n- [x] held-question - Chose scope (hold-kind: captain) (done 2026-10-23)\n- [x] reportless - SCOUT No report (done 2026-10-23)\n## In flight\n- [ ] old - Reopened task\n')
        rows, _, _ = flow.delivery_records(self.home)
        self.assertFalse({'question', 'held-question', 'old', 'reportless'} & {r['id'] for r in rows})

    def test_curated_recap_full_report_and_linked_receipt_are_plain_text(self):
        rows, _, _ = flow.delivery_records(self.home)
        text = flow.delivery_content(next(r for r in rows if r['id'] == 'old'), self.home)
        for part in ('Recap', 'Only the skill authoring', 'Evals not run', 'END_OF_REPORT', 'RECEIPT_END', '2026-10-21', 'Scope: this task only'):
            self.assertIn(part, text)

    def test_missing_recap_and_artifacts_have_clear_fallback(self):
        text = flow.delivery_content({'id': 'missing', 'title': 'Recorded study', 'state': 'done', 'verb': 'reported'}, self.home)
        self.assertIn('No recap yet', text)
        self.assertIn('No full report for this task yet', text)
        self.assertIn('date unavailable', text)
        self.assertNotIn('no agent yet', text)

    def test_links_cannot_read_other_tasks_traverse_symlinks_or_execute(self):
        secret = self.root / 'data/secret.md'
        secret.write_text('PRIVATE_SENTINEL')
        (self.root / 'data/old/leak.md').symlink_to(secret)
        (self.root / 'data/old/nested').symlink_to(self.root / 'data')
        (self.root / 'data/old/report.md').write_text('[outside](../secret.md)\n[absolute](' + str(secret) + ')\n[symlink](leak.md)\n[nested](nested/secret.md)\n[url](https://example.invalid/no-open.md)\n[shell](file:///tmp/nope.md)\n<script>alert(1)</script>\n\x1b[31mNO_ANSI\x1b[0m\n[command]($(touch /tmp/deck-never-run).md)\n')
        text = flow.delivery_content({'id': 'old'}, self.home)
        self.assertNotIn('PRIVATE_SENTINEL', text)
        self.assertIn('Artifact refused', text)
        self.assertIn('Artifact unavailable', text)
        self.assertIn('External link (text only', text)
        self.assertIn('<script>alert(1)</script>', text)
        self.assertNotIn('\x1b', text)
        for path in ('../data/secret.md', 'data/old/../secret.md', str(self.root / 'data/old/../secret.md')):
            with self.assertRaises(ValueError):
                flow.delivery_read(self.home.path, path, 4096)
        self.assertIn('Unsafe task path', flow.delivery_content({'id': '../secret'}, self.home))

    def test_file_size_and_fifo_limits_are_visible(self):
        (self.root / 'data/old/report.md').write_bytes(b'x' * (flow.DELIVERY_FILE_LIMIT + 1))
        os.mkfifo(self.root / 'data/old/receipt.json.fifo')
        with self.assertRaises(ValueError):
            flow.delivery_read(self.home.path, 'data/old/receipt.json.fifo', 4096)
        self.assertIn('byte read limit', flow.delivery_content({'id': 'old'}, self.home))

    def test_missing_backend_reader_and_unsupported_backend_are_explicit(self):
        with patch.object(flow.shutil, 'which', return_value=None):
            rows, note, complete = flow.delivery_records(self.home)
        self.assertFalse(complete)
        self.assertEqual(rows, [])
        self.assertIn('tasks-axi', note)
        (self.root / '.tasks.toml').write_text('backend = "beads"\n')
        rows, note, complete = flow.delivery_records(self.home)
        self.assertFalse(complete)
        self.assertEqual(rows, [])
        self.assertIn('doesn’t share finished tasks with Deck', note)
        remote = flow.Home('remote', self.home.path, 'secondmate', route='remote')
        rows, note, complete = flow.delivery_records(remote)
        self.assertFalse(complete)
        self.assertIn('remote crews don’t share history', note)
        self.assertIn('This crew is remote', flow.delivery_content({'id': 'old'}, remote))

    def test_configured_archive_and_unsafe_backend_paths(self):
        (self.root / 'data/retained.md').write_text((self.root / 'data/done-archive.md').read_text())
        config = self.root / '.tasks.toml'
        config.write_text(config.read_text().replace('data/done-archive.md', 'data/retained.md'))
        rows, note, complete = flow.delivery_records(self.home)
        self.assertTrue(complete)
        self.assertEqual(len(rows), 32)
        self.assertIn('retained.md', note)
        config.write_text(config.read_text().replace('data/retained.md', '../escape.md'))
        rows, note, complete = flow.delivery_records(self.home)
        self.assertFalse(complete)
        self.assertEqual(len(rows), 1)
        self.assertIn('unsafe traversal', note)
        self.assertIn('incomplete', note)

    def test_only_explicit_open_marks_read_and_it_survives_new_ui(self):
        ui, snap = self.ui()
        self.assertIn('new', self.frame(ui, snap))
        self.assertFalse(flow.delivery_seen(self.home.path, 'old'))
        flow.parse_input(b'H', ui)
        self.frame(ui, snap)
        self.assertFalse(flow.delivery_seen(self.home.path, 'old'))
        flow.parse_input(b'old\r', ui)
        self.assertIsNotNone(ui.delivery)
        self.assertTrue(flow.delivery_seen(self.home.path, 'old'))
        other, _ = self.ui()
        self.assertTrue(flow.delivery_seen(self.home.path, 'old'))
        self.assertFalse(flow.delivery_seen(str(self.root / 'another-home'), 'old'))
        self.assertEqual(other.delivery, None)

    def test_enter_click_and_d_open_delivery_never_agent_or_submit(self):
        ui, snap = self.ui()
        with patch.object(ui, 'open_selected') as pane, patch.object(ui, 'dialog_submit') as submit:
            flow.parse_input(b'\r', ui)
            self.assertIn('Installed skill', ui.delivery)
            pane.assert_not_called()
            flow.parse_input(b'\x1b', ui)
            self.frame(ui, snap)
            x1, y1, _, _, card = next(r for r in ui.card_regions if r[4].bucket == 'landed')
            ui.click(x1 + 1, y1 + 1, 0)
            self.assertIsNotNone(ui.delivery)
            flow.parse_input(b'\x1bd', ui)
            self.assertIsNotNone(ui.delivery)
            submit.assert_not_called()
        self.assertIn('read · Delivery', flow.UI.card_agent_line(card))

    def test_delivery_full_text_scroll_and_mouse_at_small_sizes(self):
        for width, height in ((130, 15), (80, 12), (60, 10), (40, 10)):
            ui, snap = self.ui()
            flow.parse_input(b'\r', ui)
            flow.parse_input(b'r', ui)
            seen = self.frame(ui, snap, width, height)
            ui.click(5, 5, 65)
            self.assertGreater(ui.delivery_scroll, 0)
            while ui.delivery_scroll < ui.delivery_max:
                flow.parse_input(b'\x1b[6~', ui)
                seen += self.frame(ui, snap, width, height)
            self.assertIn('END_OF_REPORT', seen)
            flow.parse_input(b'd', ui)
            seen += self.frame(ui, snap, width, height)
            while ui.delivery_scroll < ui.delivery_max:
                flow.parse_input(b'\x1b[6~', ui)
                seen += self.frame(ui, snap, width, height)
            self.assertIn('RECEIPT_END', seen)
            flow.parse_input(b'\x1b[H', ui)
            self.assertEqual(ui.delivery_scroll, 0)
            self.frame(ui, snap, width, height)
            x1, _, y, _ = next(hit for hit in ui.reader_hits if hit[3] == 'back')
            ui.click(x1, y, 0)
            self.assertEqual(ui.delivery_view, 'summary')
            self.frame(ui, snap, width, height)
            x1, _, y, _ = next(hit for hit in ui.reader_hits if hit[3] == 'back')
            ui.click(x1, y, 0)
            self.assertIsNone(ui.delivery)

    def test_history_search_keyboard_mouse_and_paging(self):
        ui, snap = self.ui()
        flow.parse_input(b'H', ui)
        self.frame(ui, snap)
        self.assertEqual(len(ui.history_rows()), 32)
        flow.parse_input(b'\x1b[6~', ui)
        self.assertGreater(ui.history['index'], 0)
        flow.parse_input(b'\x15evals', ui)
        self.assertEqual([r['id'] for r in ui.history_rows()], ['old'])
        self.frame(ui, snap)
        x1, _, y, _ = ui.history_hits[0]
        ui.click(x1, y, 0)
        self.assertNotIn('END_OF_REPORT', ui.delivery)
        flow.parse_input(b'r', ui)
        self.assertIn('END_OF_REPORT', ui.delivery)
        flow.parse_input(b'\x1b\x15no-such-result', ui)
        self.assertIn('No matching deliveries', self.frame(ui, snap))
        self.assertFalse(ui.quitting)

    def test_history_source_info_is_scrollable_and_never_marks_read(self):
        ui, snap = self.ui()
        (self.root / '.tasks.toml').write_text('backend = "beads"\n')
        flow.parse_input(b'H', ui)
        self.assertIn('incomplete sources', self.frame(ui, snap))
        flow.parse_input(b'\t', ui)
        self.assertIn('doesn’t share finished tasks with Deck', ui.delivery)
        self.assertFalse(flow.delivery_seen(self.home.path, 'old'))
        seen = self.frame(ui, snap, 60, 12)
        while ui.delivery_scroll < ui.delivery_max:
            flow.parse_input(b'\x1b[6~', ui)
            seen += self.frame(ui, snap, 60, 12)
        self.assertIn('never send messages', seen)

    def test_stale_recent_card_displays_reopened_canonical_state(self):
        ui, snap = self.ui()
        (self.root / 'data/backlog.md').write_text('## In flight\n- [ ] old - Reopened task (kind: ship)\n')
        ui.activate()
        self.assertIn('historical', ui.delivery)
        flow.parse_input(b'd', ui)
        self.assertIn('State: in_flight', ui.delivery)
        self.assertIn('Title: Reopened task', ui.delivery)
        self.assertNotIn('State: landed', ui.delivery)
        self.assertEqual(flow.delivery_records(self.home)[0][0]['id'], 'history-29')

    def test_open_delivery_survives_recent_row_disappearing(self):
        ui, snap = self.ui()
        ui.activate()
        snap.cols['landed'] = []
        self.assertIn('Delivery', self.frame(ui, snap))
        self.assertIsNotNone(ui.delivery)

    def test_stale_pane_cannot_focus_a_retired_tab(self):
        ui, snap = self.ui()
        card = snap.cols['landed'][0]
        card.pane_id, card.tab_id = 'retired:p1', 'retired-tab'
        with patch.object(ui, '_focus') as focus:
            ui.open_selected()
            focus.assert_not_called()
        snap.panes[card.pane_id] = {'tab_id': 'live-tab'}
        card.tab_id = 'live-tab'
        ui.activate()
        with patch.object(ui, '_focus', return_value=True) as focus:
            flow.parse_input(b'o', ui)
            focus.assert_called_once()

    def test_remote_same_path_never_reads_local_artifacts_or_shares_marks(self):
        ui, snap = self.ui()
        remote = flow.Home('remote', self.home.path, 'secondmate', route='host-a', via=self.home.path)
        snap.homes.append(remote)
        snap.home = remote
        snap.cols, snap.totals = flow.make_cards(remote, {'landed': [{'id': 'old', 'what': 'Remote delivery'}]}, {}, {}, {}, snap.homes)
        ui.activate()
        self.assertIn('This crew is remote', ui.delivery)
        self.assertNotIn('END_OF_REPORT', ui.delivery)
        self.assertFalse(flow.delivery_seen(self.home.path, 'old'))
        self.assertTrue(flow.delivery_seen(flow.delivery_home_identity(remote), 'old'))
        other = flow.Home('remote-b', self.home.path, 'secondmate', route='host-b', via=self.home.path)
        self.assertFalse(flow.delivery_seen(flow.delivery_home_identity(other), 'old'))

    def test_unresolved_owner_cannot_read_active_home_artifacts(self):
        ui, snap = self.ui()
        card = snap.cols['landed'][0]
        card.owner = 'missing-owner'
        with patch.object(flow, 'delivery_content') as content:
            ui.activate()
            content.assert_not_called()
        self.assertIn('Can’t tell which crew', ui.delivery)


if __name__ == '__main__':
    unittest.main()
