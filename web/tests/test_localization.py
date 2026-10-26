"""Public defaults, instance isolation, and presentation-only localization."""
import http.client
import json
import os
import pickle
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge
import serve
from localization import Localization, normalize_language


class LocaleTests(unittest.TestCase):
    def test_explicit_languages_regions_and_safe_english_fallback(self):
        for value, expected in [('', 'en'), (None, 'en'), ('en', 'en'),
                                (' ES_mx ', 'es-MX'), ('es-419', 'es-419'), ('en-gb', 'en-GB'),
                                ('fr-FR', 'en'), ('auto', 'en'), ('../es', 'en'),
                                ('es-invalid', 'en'), ('es-MX-u-ca-gregory', 'en')]:
            with self.subTest(value=value):
                self.assertEqual(normalize_language(value), expected)
                loc = Localization(value)
                self.assertEqual(loc.t('Board'), 'Tablero' if expected.startswith('es') else 'Board')
                self.assertEqual(loc.t('New English message'), 'New English message')

    def test_catalogs_have_matching_placeholders_and_safe_substitution(self):
        from string import Formatter
        en, es = Localization('en'), Localization('es')
        self.assertEqual(en.messages.keys(), es.messages.keys())
        for key in en.messages:
            fields = lambda text: {name for _, name, _, _ in Formatter().parse(text) if name is not None}
            self.assertEqual(fields(en.messages[key]), fields(es.messages[key]), key)
        payload = '<script>{scope}</script> español 漢字'
        self.assertEqual(es.t('{label}: {path}\n{text}', label='x', path='url', text=payload), 'x: url\n' + payload)
        self.assertEqual(en.receipt_message('Respuesta guardada por Firstmate.'), 'Answer saved by Firstmate.')
        self.assertEqual(es.receipt_message('Answer saved by Firstmate.'), 'Respuesta guardada por Firstmate.')

    def test_native_config_persistence_precedence_and_independent_servers(self):
        with tempfile.TemporaryDirectory(prefix='deck locale ') as tmp:
            root = Path(tmp)
            env = dict(os.environ, CD_WEB_LANGUAGE='en', HERDR_BIN_PATH=sys.executable)
            configs = []
            for name, language in [('english', None), ('spanish', 'es-MX')]:
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
                data = {'CD_WEB_PORT': str(port), 'CD_WEB_BIND': '127.0.0.1',
                        'CD_WEB_RUNTIME': str(root / name / 'runtime'),
                        'CD_WEB_DATA': str(root / name / 'data'),
                        'CD_WEB_ORIGIN': '', 'CD_WEB_USERS': '', 'CD_WEB_HERDR_ORIGIN': '',
                        'FM_FLOW_HOMES_JSON': '{}', 'FM_FLOW_HOMES_ONLY': '1',
                        'HERDR_PLUGIN_CONFIG_DIR': str(root / name / 'config'),
                        'HERDR_PLUGIN_STATE_DIR': str(root / name / 'state')}
                if language: data['CD_WEB_LANGUAGE'] = language
                path = root / (name + '.json'); path.write_text(json.dumps(data), encoding='utf-8')
                configs.append((path, port))

            def invoke(action, config):
                result = subprocess.run([sys.executable, serve.__file__, action, '--config', str(config)],
                                        env=env, capture_output=True, text=True, timeout=25)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            def read(port):
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                conn.request('GET', '/api/locale', headers={'Accept-Language': 'es-ES,es'})
                response = conn.getresponse(); self.assertEqual(response.status, 200)
                data = json.loads(response.read()); conn.close(); return data

            running = []
            try:
                for config, port in configs:
                    invoke('start', config); running.append(config)
                for _ in range(2):
                    self.assertEqual(read(configs[0][1])['locale'], 'en')
                    spanish = read(configs[1][1]); self.assertEqual(spanish['locale'], 'es-MX')
                    self.assertEqual(spanish['messages']['Board'], 'Tablero')
                invoke('stop', configs[1][0]); running.remove(configs[1][0])
                invoke('start', configs[1][0]); running.append(configs[1][0])
                self.assertEqual(read(configs[1][1])['locale'], 'es-MX')
                self.assertEqual(read(configs[0][1])['locale'], 'en')
            finally:
                for config in running: invoke('stop', config)


@unittest.skipIf(os.name == 'nt', 'Live Firstmate fixture endpoints require POSIX/WSL')
class LocalizedOwnerTests(unittest.TestCase):
    def setUp(self):
        from fixtures import fixture
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, HERDR_PLUGIN_STATE_DIR=str(self.root / 'state'))
        self.env.start(); self.addCleanup(self.env.stop)
        self.en, self.home = fixture(self.root)
        self.en.i18n = Localization('en'); self.en.t = self.en.i18n.t
        self.es = bridge.Board(self.en.data_dir, collector=self.en.collector, language='es-419')

    def test_language_changes_only_presentation_not_tasks_or_decision_identity(self):
        before = pickle.dumps(self.en.collector.snap)
        eng = self.en.board(); spanish = self.es.board()
        self.assertEqual(eng['columns'][1]['title'], 'Underway')
        self.assertEqual(spanish['columns'][1]['title'], 'En curso')
        for left, right in zip(eng['columns'][1]['cards'], spanish['columns'][1]['cards']):
            for key in ('task', 'title', 'original_title', 'dependencies', 'report'):
                # report date provenance is presentation; original report text stays intact.
                if key == 'report': self.assertEqual(left[key]['text'], right[key]['text'])
                else: self.assertEqual(left[key], right[key])
            for key in ('reason', 'waiting_for', 'next_action'):
                self.assertEqual(left['context'][key], right['context'][key])
            self.assertEqual(left['native']['state'], right['native']['state'])
            self.assertEqual(left['native']['herdr'], right['native']['herdr'])
            self.assertNotEqual(left['context']['label'], right['context']['label'])
        card = self.en.collector.snap.cols['captains_call'][0]; key = self.en.key(card)
        a, b = self.en.ticket(key)['call'], self.es.ticket(key)['call']
        for field in ('options', 'recommend', 'occurrence', 'identity', 'decide', 'close'):
            self.assertEqual(a[field], b[field])
        self.assertEqual(b['presentation']['options'][-1]['value'], 'reconcile')
        self.assertEqual(b['presentation']['options'][-1]['label'], 'Reconciliar')
        self.assertEqual(a['presentation']['options'][0], b['presentation']['options'][0])
        self.assertEqual(pickle.dumps(self.en.collector.snap), before)
        with patch.object(bridge.Board, 'guarded_intake', return_value=False):
            english_error = self.en.ticket(key)['call']
            spanish_error = self.es.ticket(key)['call']
            self.assertEqual(english_error['occurrence'], spanish_error['occurrence'])
            self.assertIn('occurrence guard', english_error['error'])
            self.assertIn('guard de ocurrencia', spanish_error['error'])
        note = 'Mi respuesta {unchanged}: café 漢字 <script>'
        receipt = self.es.submit(key, {'value': 'scope', 'note': note, 'occurrence': b['occurrence'],
                                      'request_id': 'l' * 32}, 'local')
        self.assertTrue(receipt['saved']); self.assertEqual(receipt['message'], 'Respuesta guardada por Firstmate.')
        mutation = json.loads((Path(self.home.path) / 'mutations.jsonl').read_text())
        self.assertIn('--expected-identity', mutation['args'])
        self.assertIn(note, mutation['input']); self.assertIn('scope', mutation['input'])
        self.assertEqual(self.en.receipts('local')[0]['message'], 'Answer saved by Firstmate.')
        self.assertEqual(len((Path(self.home.path) / 'mutations.jsonl').read_text().splitlines()), 1)

    def test_delivery_history_and_fallbacks_keep_authored_content(self):
        home = bridge.home_id(self.home)
        en, es = self.en.delivery(home, 'demo-research'), self.es.delivery(home, 'demo-research')
        self.assertEqual(en['summary'], es['summary']); self.assertEqual(en['report'], es['report'])
        self.assertIn('FIN_DEL_INFORME', es['report'])
        self.assertEqual(en['report'], bridge.fm.readable_markdown((Path(self.home.path) / 'data/demo-research/report.md').read_text()))
        self.assertIn('<script>', es['report'])
        self.assertTrue(en['details'].startswith('Delivery details'))
        self.assertTrue(es['details'].startswith('Detalles de la entrega'))
        self.assertEqual(en['time']['date'], es['time']['date'])
        self.assertIsNone(en['time']['epoch']); self.assertIn('sólo fecha', es['time']['detail'])
        self.assertIn('Retained Firstmate history', self.en.history()['details'][0]['source'])
        self.assertIn('Historial retenido de Firstmate', self.es.history()['details'][0]['source'])
        # A generated no-report message translates; a report containing that same
        # sentence would remain authored text in both locales.
        for f in ('delivery.md', 'recap.md', 'report.md'):
            (Path(self.home.path) / 'data/demo-research' / f).unlink(missing_ok=True)
        self.assertIn('No summary is retained.', self.en.delivery(home, 'demo-research')['summary'])
        self.assertIn('No hay un resumen retenido.', self.es.delivery(home, 'demo-research')['summary'])

    def test_errors_are_localized_at_the_same_http_guard(self):
        server = bridge.Server(('127.0.0.1', 0), self.es, bridge.Security(1))
        server.security = bridge.Security(server.server_port)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            conn.request('GET', '/api/locale', headers={'Sec-Fetch-Site': 'cross-site'})
            response = conn.getresponse(); self.assertEqual(response.status, 403)
            self.assertEqual(json.loads(response.read())['error'], 'Petición de otro sitio rechazada.')
            conn.close()
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
