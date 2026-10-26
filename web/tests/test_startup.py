"""Poll the real collector during a delayed, isolated first source read."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from fixtures import bridge, fixture


class StartupTests(unittest.TestCase):
    def test_loading_and_health_follow_the_first_source_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recorded, home = fixture(root)
            # Discovery uses the deployed env format, pointing only at this
            # temporary home. No installed endpoint or Herdr call is used.
            (Path(home.path) / 'bin/fm-bearings-snapshot.sh').write_text('#!/bin/sh\nexit 1\n')
            payload = recorded.collector._raw[home.label][0]
            source_started = threading.Event()
            release_source = threading.Event()
            published = threading.Event()
            finished = threading.Event()

            def read_source(selected):
                self.assertEqual(selected.path, home.path)
                source_started.set()
                if not release_source.wait(timeout=5):
                    raise RuntimeError('test source was not released')
                return payload

            env = {'FM_FLOW_HOMES': 'firstmate=' + home.path,
                   'FM_FLOW_HOMES_ONLY': '1',
                   'HERDR_PLUGIN_CONFIG_DIR': str(root / 'config'),
                   'HERDR_PLUGIN_STATE_DIR': str(root / 'model')}
            with patch.dict(os.environ, env), \
                    patch.object(bridge.fm, '_lease_holders', return_value={}), \
                    patch.object(bridge.fm, '_presentation_labels', return_value={}), \
                    patch.object(bridge.fm, '_live_agent_homes', return_value=set()), \
                    patch.object(bridge.fm, '_git_revision', return_value='fixture'), \
                    patch.object(bridge.fm, 'herdr_state', return_value={
                        'agents': {}, 'panes': {}, 'workspaces': {}, 'tabs': {}}), \
                    patch.object(bridge.fm, 'bearings_snapshot', side_effect=read_source):
                board = bridge.Board(root / 'web-data')
                collector = board.collector
                original_publish, original_run = collector._publish, collector.run

                def publish(snapshot):
                    original_publish(snapshot)
                    if snapshot.collected_at:
                        published.set()

                def run():
                    try:
                        original_run()
                    finally:
                        finished.set()

                collector._publish, collector.run = publish, run
                server = bridge.Server(('127.0.0.1', 0), board, bridge.Security(8787))
                port = server.server_port
                server.security = bridge.Security(port)
                http_thread = threading.Thread(target=server.serve_forever, daemon=True)
                http_thread.start()

                def get(path):
                    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                    connection.request('GET', path)
                    response = connection.getresponse()
                    status, data = response.status, json.loads(response.read())
                    connection.close()
                    return status, data

                try:
                    board.start()
                    self.assertTrue(source_started.wait(timeout=5))
                    status, health = get('/health')
                    self.assertEqual(status, 503)
                    self.assertFalse(health['ok'])
                    self.assertEqual(health['collector_at'], 0)
                    self.assertTrue(health['loading'])
                    self.assertEqual(health['error'], '')
                    status, loading = get('/api/board')
                    self.assertEqual(status, 200)
                    self.assertTrue(loading['loading'])
                    self.assertEqual(loading['collected_at'], 0)
                    self.assertEqual(loading['homes'], [])

                    release_source.set()
                    self.assertTrue(published.wait(timeout=5))
                    status, health = get('/health')
                    self.assertEqual(status, 200)
                    self.assertTrue(health['ok'])
                    self.assertGreater(health['collector_at'], 0)
                    self.assertFalse(health['loading'])
                    status, ready = get('/api/board')
                    self.assertEqual(status, 200)
                    self.assertEqual(len(ready['homes']), 1)
                    self.assertEqual(ready['homes'][0]['label'], 'firstmate')
                    self.assertTrue(any(c['cards'] for c in ready['columns']))
                    self.assertEqual(ready['error'], '')
                    status, history = get('/api/history?q=Demo%20research')
                    self.assertEqual(status, 200)
                    self.assertEqual(len(history['records']), 1)

                    # A published collector error is also unready, rather
                    # than a successful empty board after failed discovery.
                    board.stop()
                    self.assertTrue(finished.wait(timeout=5))
                    failed = bridge.fm.Snapshot()
                    failed.error = 'fixture discovery failed'
                    collector._publish(failed)
                    status, health = get('/health')
                    self.assertEqual(status, 503)
                    self.assertFalse(health['ok'])
                    self.assertEqual(health['error'], failed.error)
                finally:
                    release_source.set()
                    board.stop()
                    self.assertTrue(finished.wait(timeout=5))
                    server.shutdown()
                    server.server_close()
                    http_thread.join(timeout=2)
            self.assertFalse((Path(home.path) / 'mutations.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
