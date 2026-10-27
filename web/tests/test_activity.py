"""The board says what is happening now, from the reproduced busy-looking fleet."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import activity_fleet
from fixtures import bridge

f = bridge.fm


class ActivityTruthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, HERDR_PLUGIN_STATE_DIR=str(Path(self.tmp.name) / 'state'))
        env.start()
        self.addCleanup(env.stop)

    def board(self, firstmate_fields=False):
        root = Path(self.tmp.name) / ('with-fields' if firstmate_fields else 'today')
        return activity_fleet.fleet(root, firstmate_fields)[0]

    def cards(self, board):
        return {c['task']: c for col in board.board()['columns'] for c in col['cards']}

    def test_only_agents_reported_working_count_as_working_now(self):
        cards = self.cards(self.board())
        expected = {
            'api-pagination': ('working', 'working'), 'search-index': ('working', 'working'),
            'docs-refresh': ('working', 'working'),
            # crew state reports the validation run as working; it is not the agent.
            'release-notes': ('validating', 'validation'),
            'billing-export': ('validation_gate', 'present'),
            'cache-migration': ('paused', 'none'), 'log-rotation': ('paused', 'none'),
            'notes-sync': ('finished', 'present'), 'mobile-publish': ('blocked', 'present'),
        }
        self.assertEqual({k: (cards[k]['activity']['kind'], cards[k]['activity']['agent']) for k in expected}, expected)
        underway = self.board().collector.snap.cols['underway']
        self.assertEqual(len(underway), 9)
        self.assertEqual(f.activity_counts(underway)['working'], 3)

    def test_queued_ready_is_distinct_from_blocked_and_deferred(self):
        cards = self.cards(self.board())
        kinds = {k: cards[k]['activity']['kind'] for k in
                 ('invoice-email', 'pricing-page', 'pricing-followup', 'device-trial', 'pilot-rollout', 'readme-badges')}
        self.assertEqual(kinds, {'invoice-email': 'blocked', 'pricing-page': 'blocked', 'pricing-followup': 'blocked',
                                 'device-trial': 'held', 'pilot-rollout': 'held', 'readme-badges': 'ready'})
        self.assertEqual(cards['scope-choice']['activity']['kind'], 'decision')

    def test_hold_and_pause_dates_come_only_from_exported_fields(self):
        board = self.board()
        today = self.cards(board)
        # The gate reason says "until <date>", but prose is never parsed for a date.
        gate = next(c for c in board.collector.snap.cols['charted'] if c.task == 'device-trial')
        self.assertTrue(gate.doing.startswith('until '))
        for task, gap in (('device-trial', 'hold_until'), ('cache-migration', 'wait_until'), ('log-rotation', 'wait_until')):
            self.assertEqual((today[task]['activity']['until'], today[task]['activity']['gap']), ('', gap), task)
        present = self.cards(self.board(firstmate_fields=True))
        self.assertEqual(present['device-trial']['activity']['kind'], 'deferred')
        self.assertRegex(present['device-trial']['activity']['until'], r'^\d{4}-\d{2}-\d{2}$')
        self.assertEqual(present['cache-migration']['activity']['kind'], 'paused')
        self.assertTrue(present['cache-migration']['activity']['until'].endswith('Z'))
        self.assertFalse(any(c['activity']['gap'] for c in present.values()))

    def test_a_passed_pause_date_or_hold_is_not_presented_as_current(self):
        card = next(c for c in self.board(True).collector.snap.cols['underway'] if c.task == 'cache-migration')
        card.wait_until = '2020-01-01T00:00:00Z'
        self.assertEqual(f.card_activity(card)['until'], '')
        gate = next(c for c in self.board().collector.snap.cols['charted'] if c.task == 'device-trial')
        gate.hold_until = '2020-01-01'
        self.assertEqual(f.card_activity(gate)['kind'], 'held')

    def test_state_source_separates_agent_from_validation_when_flow_source_is_replaced(self):
        card = next(c for c in self.board(True).collector.snap.cols['underway'] if c.task == 'release-notes')
        card.flow = dict(card.flow, source='backlog.hold_reason', reason='Held for review')
        card.doing = 'Held for review'
        self.assertEqual(f.card_activity(card)['kind'], 'validating')
        card.state_source = ''
        card.run_step = {}
        # Without either signal Firstmate's working verdict is reported as working.
        self.assertEqual(f.card_activity(card)['kind'], 'working')

    def test_missing_endpoint_export_degrades_to_unknown_presence(self):
        data = activity_fleet.payload()
        data.pop('endpoints')
        home = f.Home('Firstmate', str(Path(self.tmp.name) / 'home'), 'captain')
        cols, _ = f.make_cards(home, data, {}, {}, {}, [home])
        agents = {c.task: f.card_activity(c)['agent'] for c in cols['underway']}
        self.assertEqual(agents['cache-migration'], 'unknown')
        self.assertEqual(agents['notes-sync'], 'unknown')
        self.assertEqual(agents['api-pagination'], 'working')

    def test_registered_execution_names_the_executor_without_task_meta(self):
        cards = self.cards(self.board())
        self.assertEqual(cards['search-index']['presentation']['executor'], {'harness': 'pi'})
        self.assertEqual(cards['readme-badges']['presentation']['executor'], {'harness': ''})

    def test_language_changes_labels_only(self):
        root = Path(self.tmp.name) / 'es'
        spanish = self.cards(activity_fleet.fleet(root, language='es')[0])
        english = self.cards(self.board())
        self.assertEqual({k: v['activity'] for k, v in spanish.items()}, {k: v['activity'] for k, v in english.items()})

    def test_terminal_underway_heading_counts_agents_working_now(self):
        board = self.board()
        snap = board.collector.snap
        ui = f.UI()
        ui.collector.snapshot = lambda: snap
        frames = []
        with patch.object(f.shutil, 'get_terminal_size', return_value=os.terminal_size((184, 40))), \
                patch.object(ui, 'paint', side_effect=lambda rows, *_: frames.append(rows)):
            ui.render(snap, force=True)
        text = '\n'.join(f._ANSI_RE.sub('', row) for row in frames[-1])
        self.assertIn('Underway (9 · ●3 working)', text)


if __name__ == '__main__':
    unittest.main()
