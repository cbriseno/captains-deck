"""The terminal board follows the web's per-installation language setting."""
import importlib.util
import json
import os
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('language_fixture', HERE / 'test_flow_clarity.py')
clarity = importlib.util.module_from_spec(spec); spec.loader.exec_module(clarity)
f = clarity.f
ROOT = HERE.parent
SPANISH = json.loads((ROOT / 'web/locales/es.json').read_text(encoding='utf-8'))
# Catalog entries whose Spanish differs from the English source message.
SPANISH_LABELS = {v.strip() for k, v in SPANISH.items() if v != k and len(v.strip()) >= 5}
# Authored fixture text, and the words both languages show as they are:
# product, key and protocol-state names, the loanwords the Spanish keeps and
# words spelled the same in both.
AUTHORED = 'Zorblat flux glim quux Flarn one two blip fixture'
NEUTRAL = {'firstmate', 'herdr', 'deck', 'captain', 'call', 'lavish', 'esc', 'enter', 'tab', 'pgup', 'pgdn',
           'paused', 'unix', 'utc', 'harness', 'worker', 'bytes', 'chat', 'control', 'decide', 'local',
           'supervisor', 'terminal', 'flow', 'ctrl'}
JARGON = re.compile(r'intake|\bhomes?\b|backend|retain|retention|retenid|retenci|occurrence|ocurrencia|receipt|recibo|'
                    r'\bguard\b|identifier|identificador|ledger|snapshot|owner|reconcil|conciliaci', re.I)


def words(text):
    return {w.lower() for w in re.findall(r'[^\W\d_]{3,}', text)}


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = clarity.fixture.fixture(self.root)
        clarity.install_explanation_endpoint(self.root)
        self.config = self.root / 'plugin-config'
        self.config.mkdir()
        env = {k: v for k, v in os.environ.items() if k != 'CD_WEB_LANGUAGE'}
        env.update(FM_FLOW_SHOW_LANDED='1', HERDR_PLUGIN_STATE_DIR=str(self.root / 'plugin-state'),
                   HERDR_PLUGIN_CONFIG_DIR=str(self.config))
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        f._LOCALIZATION = None
        self.addCleanup(setattr, f, '_LOCALIZATION', None)
        self.render = lambda ui, snap, w=240, h=46: clarity.ClarityTests.render(self, ui, snap, w, h)

    def board_text(self):
        """Every TUI surface that used to carry fixed Spanish wording."""
        (self.root / 'state/task.status').write_text('paused [at=1792920000]: Waiting on the fixture\n')
        payload = {'in_flight': [{'id': 'task', 'name': 'Fixture work', 'state': 'paused'},
                                 {'id': 'ready', 'name': 'Ready work', 'state': 'done'}],
                   'landed': [{'id': 'old', 'what': 'Landed fixture', 'owner': '(main)', 'artifact': '-'}]}
        ui, snap = clarity.ClarityTests.ui(self, payload)
        card = snap.cols['underway'][0]
        card.dependencies = [{'id': 'dep', 'title': 'Upstream fix', 'owner': 'mate',
                              'unresolved': False, 'reconciliation_required': True}]
        texts = [self.render(ui, snap), self.render(ui, snap, 60, 18)]
        texts.append(str(f.UI.detail_fields(card)))
        texts.append(f.dependency_caption(card))
        texts.append(f.supervisor_caption({}))
        texts.append(f.supervisor_caption({'state': 'waiting', 'observed_at': 1792920000, 'source': 'fixture'}))
        texts.append(f.local_time_caption(f.CardTime('Delivered')))
        ui.open_help(); texts.append(self.render(ui, snap)); ui.close_help()
        ui.open_delivery(snap.cols['landed'][0])
        ui.delivery_explain()
        texts.append(self.render(ui, snap))
        ui.delivery_set_view('details'); texts.append(self.render(ui, snap))
        ui.delivery_back(); ui.delivery_back()
        f.parse_input(b'H', ui); texts.append(self.render(ui, snap))
        return '\n'.join(texts)

    def terminal_surfaces(self, language):
        """The terminal Captain's Call dialog, its errors, help, details, board footer,
        message box, history reader and status line, as shown."""
        (self.config / 'language').write_text(language + '\n')
        f._LOCALIZATION = None
        (self.root / 'state/zq-flarn.status').write_text('paused [at=1792920000]: Glim quux\n')
        call = {'question': 'Zorblat glim?', 'recommended': 'flarn', 'context': 'Zorblat flux glim',
                'options': [{'id': 'flarn', 'label': 'Flarn one', 'detail': 'Flarn quux'}, {'id': 'blip', 'label': 'Flarn two'}]}
        payload = {'in_flight': [{'id': 'zq-flarn', 'name': 'Zorblat flux', 'state': 'paused'}],
                   'decisions_open': [{'id': 'zq-glim', 'title': 'Zorblat glim', 'owner': '(main)',
                                       'reason': 'Glim quux', 'call': call}]}
        ui, snap = clarity.ClarityTests.ui(self, payload)

        def modal(draw):
            lines = [' ' * 120 for _ in range(70)]
            draw(lines, 120, 70)
            return '\n'.join(lines)

        texts = []
        ui.open_decision(snap.cols['captains_call'][0])
        d = ui.dialog
        texts.append(modal(ui.render_dialog))
        d.selected, d.note = -1, ''
        ui.dialog_submit(); texts.append(modal(ui.render_dialog))
        d.note = 'zorblat ' * 80
        ui.dialog_submit(); texts.append(modal(ui.render_dialog))
        d.note, d.selected = '', 0
        d.home_path = str(self.root / 'zq-nowhere')
        ui._submit_worker(d); texts.append(modal(ui.render_dialog))
        d.home_path = ''
        ui._submit_worker(d); texts.append(modal(ui.render_dialog))
        d.home_path, d.key = str(self.root), 'zq\nglim'
        ui._submit_worker(d); texts.append(modal(ui.render_dialog))
        d.wake_home, d.wake_lane = str(self.root), '../zq'
        texts.append(f.wake_owner(d))
        d.wake_lane = 'zq-lane'
        texts.append(f.wake_owner(d))
        ui.dialog = None
        ui.details = snap.cols['underway'][0]
        texts.append(modal(ui.render_details))
        ui.details = None
        texts.append(modal(ui.render_help))
        # The board itself: card footers and the clickable footer hints.
        texts.append(self.render(ui, snap, 240, 46))
        ui.open_message(); ui.message.text = 'Zorblat'
        texts.append(modal(ui.render_message))
        ui.message = None
        ui.open_history()
        # One authored row keeps the fixture's own history titles out of the comparison.
        ui.history['rows'] = [{'id': 'zq-blip', 'title': 'Zorblat quux', 'closed': '2026-10-01', 'source': 'archive',
                               'delivery_identity': 'zq', 'owner_label': 'fixture'}]
        texts.append(modal(ui.render_history))
        ui.history = None
        collector = f.Collector()
        collector._discover = lambda: []
        publish = collector._publish
        collector._publish = lambda shot: (publish(shot), collector._stop.set(), collector._wake.set())
        collector.run()
        empty = f.UI(); empty.collector = collector
        texts.append(self.render(empty, collector.snapshot()).splitlines()[-1])
        # File paths, file names and environment variable names are shown as they are.
        # Time zone abbreviations after a clock time follow the machine, not the language.
        return re.sub(r'\S*/\S*|\S+\.(?:sh|conf|status)\b|\b[A-Z]+(?:_[A-Z]+)+\b|(?<=\d\d:\d\d )[A-Z]{2,5}\b', ' ',
                      f._ANSI_RE.sub('', '\n'.join(texts)))

    def test_spanish_terminal_dialog_help_and_details_leave_no_english_chrome(self):
        english, spanish = self.terminal_surfaces('en'), self.terminal_surfaces('es')
        chrome = words(english) - words(AUTHORED) - NEUTRAL
        self.assertGreater(len(chrome), 60)
        self.assertEqual(sorted(chrome & words(spanish)), [])

    def test_terminal_only_messages_use_no_internal_terms(self):
        for language in ('en', 'es'):
            with self.subTest(language=language):
                text = self.terminal_surfaces(language)
                self.assertIsNone(JARGON.search(text), text)

    def test_english_is_the_default_without_configuration(self):
        self.assertEqual(f.deck_language(), '')
        text = self.board_text()
        leaked = sorted(label for label in SPANISH_LABELS if label in text)
        self.assertEqual(leaked, [])
        for label in ('On hold', 'Firstmate state: paused', '[Back]', 'Request: Queued for Firstmate',
                      'Details and sources', 'Sources: ', ' · H history', 'Enter: full text · o agent',
                      '(resolved)', 'r report; d details', 'Firstmate: current activity unknown'):
            self.assertIn(label, text)

    def test_spanish_when_the_installation_configures_it(self):
        for configure in (lambda: (self.config / 'language').write_text('es-MX\n'),
                          lambda: os.environ.__setitem__('CD_WEB_LANGUAGE', 'es')):
            with self.subTest(configure=configure):
                for leftover in (self.config / 'language',):
                    leftover.unlink(missing_ok=True)
                os.environ.pop('CD_WEB_LANGUAGE', None)
                configure()
                f._LOCALIZATION = None
                text = self.board_text()
                for label in ('En espera', 'Estado de Firstmate: paused', '[Volver]', 'Solicitud: En cola para Firstmate',
                              'Detalles y fuentes', 'Fuentes: ', ' · H historial', 'Enter: texto completo · o agente',
                              '(resuelta)', 'r informe; d detalles', 'Firstmate: actividad actual desconocida'):
                    self.assertIn(label, text)
                # Authored task content is never translated.
                self.assertIn('Fixture work', text)

    def test_history_completeness_tracks_sources_in_both_languages(self):
        cases = ('complete', 'missing-current', 'missing-archive', 'unavailable-current',
                 'unavailable-archive', 'endpoint-incomplete', 'endpoint-complete',
                 'endpoint-fallback', 'remote', 'aggregate-partial', 'aggregate-complete',
                 'aggregate-empty')
        for language in ('en', 'es'):
            os.environ['CD_WEB_LANGUAGE'] = language
            f._LOCALIZATION = None
            for case in cases:
                with self.subTest(language=language, case=case):
                    root = self.root / (language + '-' + case)
                    root.mkdir()
                    home = clarity.fixture.fixture(root)
                    snap = clarity.fixture.snapshot(home)
                    complete = case in ('complete', 'endpoint-complete', 'endpoint-fallback', 'aggregate-complete')
                    for source, name in [('current', 'backlog.md'), ('archive', 'done-archive.md')]:
                        if case in ('missing-' + source, 'unavailable-' + source) or (
                            case == 'aggregate-partial' and source == 'archive'
                        ):
                            path = root / 'data' / name
                            path.unlink()
                            if case == 'unavailable-' + source:
                                path.mkdir()
                    if case.startswith('endpoint-'):
                        (root / 'bin').mkdir()
                        (root / 'bin/fm-delivery-history.sh').write_text('cat "$FM_HOME/history.json"\n')
                        data = {'schema': 'fm-delivery-history.v1', 'home': home.path,
                                'complete': complete, 'sources': ['archive/missing-label.md'],
                                'retention': {}, 'records': [], 'limitations': ['Retained fixture only']}
                        (root / 'history.json').write_text('invalid' if case == 'endpoint-fallback' else json.dumps(data))
                    if case == 'remote':
                        home.route = 'remote'
                    if case.startswith('aggregate-'):
                        snap.home = f.Home('All', '', 'all')
                        snap.homes = [snap.home]
                        if case != 'aggregate-empty':
                            other_root = root / 'mate'
                            other_root.mkdir()
                            other = clarity.fixture.fixture(other_root)
                            other.label = 'mate'
                            snap.homes.extend([home, other])
                    ui = f.UI()
                    ui.collector.snapshot = lambda: snap
                    ui.open_history()
                    self.assertEqual(ui.history['complete'], complete)
                    frame = self.render(ui, snap)
                    self.assertIn(f.tr('current + archive') if complete else f.tr('incomplete sources'), frame)


if __name__ == '__main__':
    unittest.main()
