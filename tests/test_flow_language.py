"""The terminal board follows the web's per-installation language setting."""
import importlib.util
import json
import os
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

    def test_english_is_the_default_without_configuration(self):
        self.assertEqual(f.deck_language(), '')
        text = self.board_text()
        leaked = sorted(label for label in SPANISH_LABELS if label in text)
        self.assertEqual(leaked, [])
        for label in ('Technical hold', 'Firstmate state: paused', '[Back]', 'Request: Queued for Firstmate',
                      'Details and sources', 'Retained: ', ' · H history', 'Enter: full text · o agent',
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
                for label in ('Retención técnica', 'Estado de Firstmate: paused', '[Volver]', 'Solicitud: En cola para Firstmate',
                              'Detalles y fuentes', 'Retenido: ', ' · H historial', 'Enter: texto completo · o agente',
                              '(resuelta)', 'r informe; d detalles', 'Firstmate: actividad actual desconocida'):
                    self.assertIn(label, text)
                # Authored task content is never translated.
                self.assertIn('Fixture work', text)


if __name__ == '__main__':
    unittest.main()
