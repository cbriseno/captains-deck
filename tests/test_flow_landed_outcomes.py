"""Real-home provenance survives All, history, collisions and safe report reads."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('outcome_fixture', Path(__file__).with_name('test_flow_deliveries.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
flow = fixture.flow


def fleet_fixture(root):
    root = Path(root)
    homes, parts = [], []
    for label, result in [('firstmate', 'Installed skill; evals pending.'), ('mate', 'Study only; apps unchanged.')]:
        path = root / label
        path.mkdir()
        home = fixture.fixture(path)
        home.label = label
        home.kind = 'captain' if label == 'firstmate' else 'secondmate'
        home.mate_id = label
        task = path / 'data/old'
        (task / 'delivery.md').unlink()
        (task / 'recap.md').write_text(f'# Delivery\n\n## Result\n{result}\n\n## Scope\nOnly this component.\n\n## Why\nExplain the retained outcome.\n\n## Evidence\nTargeted checks; [Full report](report.md).\n\n## Remaining\nWider validation pending.\n')
        (task / 'report.md').write_text(f'# Full report for {label}\nFULL_REPORT_{label}\n' + 'Evidence retained.\n' * 40 + f'\nEND_{label}\n')
        homes.append(home)
    for home in homes:
        payload = {'landed': [{'id': 'old', 'what': 'Original request does not describe outcome', 'owner': '(main)',
                              'card_time': {'kind': 'delivered', 'value': '2026-10-21', 'precision': 'date',
                                            'source': 'backlog.completion.done', 'event_id': home.label + ':old'}}]}
        cols, totals = flow.make_cards(home, payload, {}, {}, {}, homes)
        parts.append((home, cols, totals))
    snap = flow.Snapshot()
    snap.home = flow.Home('All', '', 'all')
    snap.homes = [snap.home] + homes
    snap.cols, snap.totals = flow.merge_fleet_columns(parts)
    return snap, parts


class LandedOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.snap, self.parts = fleet_fixture(self.root)
        env = patch.dict(os.environ, {'HERDR_PLUGIN_STATE_DIR': str(self.root / 'plugin-state'), 'FM_FLOW_SHOW_LANDED': '1'})
        env.start()
        self.addCleanup(env.stop)
        self.ui = flow.UI()
        self.ui.collector.snapshot = lambda: self.snap
        self.ui.columns = self.ui.board_columns(self.snap.home)
        self.ui.col_idx = next(i for i,(key,_) in enumerate(self.ui.columns) if key == 'landed')

    def frame(self, width=130, height=18):
        return fixture.DeliveryTests.frame(self, self.ui, self.snap, width, height)

    def test_same_real_delivery_from_all_and_specific_home(self):
        self.assertEqual(len(self.snap.cols['landed']), 2)
        firstmate, own_cols, _ = self.parts[0]
        card = own_cols['landed'][0]
        self.assertIn(card, self.snap.cols['landed'])
        self.assertEqual(card.delivery_identity, firstmate.path)
        self.assertEqual(card.delivery_summary, 'Installed skill; evals pending.')
        self.assertEqual(card.event_time.day.isoformat(), '2026-10-21')
        self.ui.open_delivery(card)
        all_text = dict(self.ui.delivery_views)
        self.snap.home = firstmate
        self.snap.cols = own_cols
        self.ui.open_delivery(card)
        self.assertEqual(all_text, self.ui.delivery_views)
        all_text = all_text['report'] + all_text['details']
        for expected in ('FULL_REPORT_firstmate', 'date only', 'backlog.completion.done', 'archive', 'State: done'):
            self.assertIn(expected, all_text)
        self.assertNotIn('FULL_REPORT_mate', all_text)
        self.assertNotIn('Home: All', all_text)
        self.assertNotIn('unsafe path', all_text)
        self.assertTrue(flow.delivery_seen(firstmate.path, 'old'))
        self.assertFalse(flow.delivery_seen(self.parts[1][0].path, 'old'))

    def test_human_first_screen_and_report_keyboard_and_mouse(self):
        card = self.parts[1][1]['landed'][0]
        for width, height in ((130, 18), (60, 16)):
            self.ui.open_delivery(card)
            frame = self.frame(width, height)
            self.assertIn('Study only; apps unchanged.', frame)
            self.assertNotIn('Task: old', frame)
            self.assertIn('Report', frame)
            flow.parse_input(b'r', self.ui)
            frame = self.frame(width, height)
            self.assertIn('FULL_REPORT_mate', frame)
            flow.parse_input(b'\x1b[H', self.ui)
            self.frame(width, height)
            x, _, y, _ = next(hit for hit in self.ui.reader_hits if hit[3] == 'report')
            self.ui.click(x, y, 0)
            self.assertIn('FULL_REPORT_mate', self.frame(width, height))

    def test_card_result_is_authored_prose_not_title_or_status(self):
        home, cols, _ = self.parts[1]
        card = cols['landed'][0]
        rendered = '\n'.join(flow._ANSI_RE.sub('', row) for row in self.ui.render_card(card, 42, False))
        self.assertIn('Study only; apps unchanged.', rendered)
        self.assertNotIn('Original request', rendered)
        (Path(home.path) / 'state/old.status').write_text('done [at=1792878000]: Bought everything; initiative completed.\n')
        (Path(home.path) / 'data/old/recap.md').unlink()
        (Path(home.path) / 'data/old/report.md').write_text('# Report\n\nPrepared cart only; no purchase.\n')
        self.assertEqual(flow.delivery_card_summary(home, 'old'), 'Prepared cart only; no purchase.')
        (Path(home.path) / 'data/old/report.md').unlink()
        self.assertEqual(flow.delivery_card_summary(home, 'old'), '')

    def test_card_gives_four_rows_to_a_long_result_with_its_qualification(self):
        card = self.parts[1][1]['landed'][0]
        card.delivery_summary = ('Investigación terminada: acceder a las apps por la Wi-Fi de casa sin Tailscale '
                                 'es viable, pero el acceso LAN todavía no está configurado.')
        lines = [flow._ANSI_RE.sub('', line) for line in self.ui.render_card(card, 45, False)]
        self.assertEqual(len(lines), flow.CARD_ROWS)
        prose = ' '.join(' '.join(line.strip(' │').split()) for line in lines[1:8])
        self.assertIn('LAN todavía no está configurado.', prose)
        self.assertIn('new · Delivery', lines[0])
        self.assertIn('2026-10-21', lines[8])

    def test_summary_precedence_empty_fallback_and_full_documents(self):
        home = self.parts[1][0]
        path = Path(home.path) / 'data/old/delivery.md'
        path.write_text('## Result\nPrepared cart; no purchase.\n\n## Scope\nOnly groceries.\n')
        self.assertEqual(flow.delivery_card_summary(home, 'old'), 'Prepared cart; no purchase.')
        text = flow.delivery_content({'id': 'old', 'title': 'Buy groceries'}, home)
        self.assertLess(text.index('Prepared cart'), text.index('Study only'))
        self.assertLess(text.index('Prepared cart'), text.index('FULL_REPORT_mate'))
        self.assertLess(text.index('FULL_REPORT_mate'), text.index('Task: old'))
        path.write_text(' \n')
        self.assertEqual(flow.delivery_card_summary(home, 'old'), 'Study only; apps unchanged.')

    def test_all_history_keeps_collisions_and_owner_search_and_read_marks(self):
        self.ui.open_history()
        self.ui.history['query'] = 'old'
        rows = self.ui.history_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r['delivery_identity'] for r in rows}, {h.path for h, _, _ in self.parts})
        for index, row in enumerate(rows):
            self.ui.history['index'] = index
            self.ui.history_open()
            self.assertNotIn('FULL_REPORT_' + row['owner_label'], self.ui.delivery)
            self.ui.delivery_report()
            self.assertIn('FULL_REPORT_' + row['owner_label'], self.ui.delivery)
            self.assertTrue(flow.delivery_seen(row['delivery_identity'], 'old'))
        self.ui.history['query'] = 'firstmate'
        self.assertEqual({r['owner_label'] for r in self.ui.history_rows()}, {'firstmate'})

    def test_history_discovers_same_day_deliveries_beyond_producer_six_row_cap(self):
        home = self.parts[0][0]
        ids = ['example-09', 'example-08', 'example-07',
               'example-06', 'example-05', 'example-04',
               'example-03', 'example-02', 'example-01']
        (Path(home.path) / 'data/backlog.md').write_text('## Done\n' + ''.join(
            f'- [x] {key} - Retained component - local main (done 2026-10-24)\n' for key in ids))
        (Path(home.path) / 'data/done-archive.md').write_text('')
        payload = {'landed': [{'id': key, 'owner': '(main)', 'closed': '2026-10-24'} for key in sorted(ids, reverse=True)[:6]]}
        cols, totals = flow.make_cards(home, payload, {}, {}, {}, [home])
        self.snap.homes = [self.snap.home, home]
        self.snap.cols, self.snap.totals = flow.merge_fleet_columns([(home, cols, totals)])
        self.assertEqual(len(self.snap.cols['landed']), 6)
        self.assertNotIn('example-02', {c.task for c in self.snap.cols['landed']})
        self.ui.open_history()
        self.assertEqual(len(self.ui.history_rows()), 9)
        for key in ids[-3:]:
            self.ui.history['query'] = key
            self.assertEqual([r['id'] for r in self.ui.history_rows()], [key])
            self.ui.history_open()
            self.ui.delivery_set_view('details')
            self.assertIn('Task: ' + key, self.ui.delivery)
            self.assertIn('date only', self.ui.delivery)

    def test_unbound_all_unknown_ambiguous_and_conflicting_owners_do_not_read(self):
        a, b = self.parts[0][0], self.parts[1][0]
        b.mate_id = a.mate_id = 'collision'
        cases = [(self.snap.home, '(main)', 'old'), (a, 'unknown', 'old'),
                 (a, 'collision', 'old'), (a, 'mate', 'firstmate/old')]
        for source, owner, task in cases:
            with self.subTest(owner=owner, task=task), patch.object(flow, 'delivery_read') as read:
                cols, _ = flow.make_cards(source, {'landed': [{'id': task, 'owner': owner}]}, {}, {}, {}, self.snap.homes)
                card = cols['landed'][0]
                self.assertEqual(card.delivery_identity, '')
                self.ui.open_delivery(card)
                read.assert_not_called()
                self.assertIn('Delivery owner unavailable', self.ui.delivery)
        self.assertEqual(flow.delivery_home_identity(self.snap.home), '')

    def test_explicit_mate_owner_survives_parent_snapshot(self):
        source, mate = self.parts[0][0], self.parts[1][0]
        cols, _ = flow.make_cards(source, {'landed': [{'id': 'old', 'owner': 'mate'}]}, {}, {}, {}, self.snap.homes)
        card = cols['landed'][0]
        self.assertEqual(card.home_path, mate.path)
        self.ui.open_delivery(card)
        self.ui.delivery_report()
        self.assertIn('FULL_REPORT_mate', self.ui.delivery)
        self.assertNotIn('FULL_REPORT_firstmate', self.ui.delivery)

    def test_summary_paths_and_linked_report_cannot_escape_home_or_task(self):
        home = self.parts[1][0]
        root = Path(home.path)
        (root / 'data/old/recap.md').write_text('Study only.\n\n[other home](' + self.parts[0][0].path + '/data/old/report.md)\n[escape](../secret.md)\n[symlink](linked.md)\n')
        (root / 'data/secret.md').write_text('PRIVATE_SENTINEL')
        (root / 'data/old/linked.md').symlink_to(root / 'data/secret.md')
        text = flow.delivery_content({'id': 'old'}, home)
        self.assertNotIn('PRIVATE_SENTINEL', text)
        self.assertNotIn('FULL_REPORT_firstmate', text)
        self.assertIn('Artifact refused', text)
        self.assertIn('Artifact unavailable', text)
        self.assertEqual(flow.delivery_card_summary(home, '../secret'), '')


if __name__ == '__main__':
    unittest.main()
