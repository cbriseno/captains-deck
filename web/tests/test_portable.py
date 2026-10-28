"""Native launcher checks use empty temporary config, never installed services."""
import http.client
import json
import os
from pathlib import Path
import socket
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import serve
import bridge
from deck_platform import endpoint_path, file_lock


class PortableTests(unittest.TestCase):
    def test_loopback_server_starts_without_hostname_resolution(self):
        # Loopback startup must also work with slow or unavailable reverse DNS.
        with patch.object(socket, 'getfqdn', side_effect=OSError('DNS unavailable')):
            with bridge.Server(('127.0.0.1', 0), None, None) as server:
                self.assertEqual(server.server_name, '127.0.0.1')
                self.assertEqual(server.server_port, server.socket.getsockname()[1])

    def test_launcher_retains_port_and_bind_validation(self):
        for args, env in [(['--port', '0'], dict(os.environ, CD_WEB_BIND='127.0.0.1')),
                          ([], dict(os.environ, CD_WEB_BIND='0.0.0.0'))]:
            result = subprocess.run([sys.executable, serve.__file__, 'run', *args], env=env,
                                    capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('error:', result.stderr)

    @unittest.skipIf(os.name == 'nt', 'Legacy wrapper is a POSIX compatibility entrypoint')
    def test_legacy_wrapper_preserves_private_environment(self):
        with tempfile.TemporaryDirectory(prefix='deck wrapper ') as tmp:
            root = Path(tmp)
            values = {
                'CD_WEB_ORIGIN': 'https://kanban.example.test', 'CD_WEB_USERS': 'owner@example.test',
                'CD_WEB_HERDR_ORIGIN': 'https://terminal.example.test',
                'CD_WEB_DATA': str(root / 'private-receipts'), 'CD_WEB_RUNTIME': str(root / 'runtime'),
                'FM_FLOW_HOMES': 'firstmate=' + str(root / 'private-home'), 'FM_FLOW_HOMES_ONLY': '1',
                'HERDR_PLUGIN_STATE_DIR': str(root / 'model-state'),
            }
            capture = root / 'capture.py'
            capture.write_text('import json,os,sys\nprint(json.dumps({"args":sys.argv[1:],"env":{k:os.environ[k] for k in ' + repr(list(values)) + '}}))\n')
            shim = root / 'python3'
            shim.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(capture)) + ' "$@"\n')
            shim.chmod(0o755)
            env = dict(os.environ, **values)
            env['PATH'] = str(root) + os.pathsep + env.get('PATH', '')
            wrapper = Path(serve.__file__).with_name('run-managed.sh')
            result = subprocess.run(['sh', str(wrapper)], env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            captured = json.loads((root / 'runtime/server.log').read_text())
            self.assertEqual(captured['env'], values)
            self.assertEqual(captured['args'], [str(Path(serve.__file__)), 'managed'])
            security = bridge.Security(8787, values['CD_WEB_ORIGIN'], values['CD_WEB_USERS'].split(','))
            self.assertEqual(security.origin, values['CD_WEB_ORIGIN'])
            self.assertEqual(security.users, {'owner@example.test'})

    @unittest.skipIf(os.name == 'nt' or not shutil.which('flock'), 'Legacy Linux flock tool unavailable')
    def test_lock_cooperates_with_legacy_flock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'server.lock'
            with file_lock(path, blocking=False):
                result = subprocess.run(['flock', '-n', '-E', '23', str(path), sys.executable, '-c', 'pass'], timeout=5)
                self.assertEqual(result.returncode, 23)
            with file_lock(path, blocking=False):
                pass

    def test_native_launcher_empty_board_and_authenticated_control(self):
        with tempfile.TemporaryDirectory(prefix='deck space ') as tmp:
            root = Path(tmp)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            config = root / 'private.json'
            config.write_text(json.dumps({
                'CD_WEB_RUNTIME': str(root / 'runtime'), 'CD_WEB_DATA': str(root / 'receipts'),
                'CD_WEB_PORT': str(port), 'CD_WEB_BIND': '127.0.0.1',
                'CD_WEB_ORIGIN': '', 'CD_WEB_USERS': '', 'CD_WEB_HERDR_ORIGIN': '',
                'FM_FLOW_HOMES_JSON': '{}', 'FM_FLOW_HOMES': '', 'FM_FLOW_HOMES_ONLY': '1',
                'HERDR_PLUGIN_CONFIG_DIR': str(root / 'config'),
                'HERDR_PLUGIN_STATE_DIR': str(root / 'state'),
            }), encoding='utf-8')
            command = [sys.executable, str(Path(serve.__file__))]

            def invoke(action):
                return subprocess.run([*command, action, '--config', str(config)],
                                      capture_output=True, text=True, timeout=20)

            try:
                start = invoke('start')
                log = root / 'runtime/server.log'
                self.assertEqual(start.returncode, 0, start.stdout + start.stderr +
                                 (log.read_text() if start.returncode and log.exists() else ''))
                first = json.loads(start.stdout)
                again = invoke('start')
                self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
                self.assertEqual(first['pid'], json.loads(again.stdout)['pid'])
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                conn.request('GET', '/api/board')
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                board = json.loads(response.read())
                conn.close()
                self.assertEqual(board['homes'], [])
                self.assertTrue(all(not c['cards'] for c in board['columns']))
                record = json.loads((root / 'runtime/control.json').read_text())
                with socket.create_connection(('127.0.0.1', record['port']), timeout=3) as sock:
                    sock.sendall(b'{"token":"wrong","command":"stop"}\n')
                    self.assertEqual(sock.recv(1024), b'')
                self.assertEqual(invoke('status').returncode, 0)
                self.assertFalse((root / 'receipts').exists())
            finally:
                stopped = invoke('stop')
            self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
            self.assertEqual(invoke('status').returncode, 1)

    def test_code_root_fallback_and_home_endpoint_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, code = root / 'owner with spaces', root / 'code'
            home.mkdir()
            (code / 'bin').mkdir(parents=True)
            endpoint = code / 'bin/fm-bearings-snapshot.sh'
            endpoint.write_text('#!/bin/sh\n')
            with patch.dict(os.environ, {
                'FM_ROOT_OVERRIDE': str(code), 'FM_FLOW_HOMES_JSON': json.dumps({'Crew': str(home)}),
                'FM_FLOW_HOMES': '', 'FM_FLOW_HOMES_ONLY': '1',
                'HERDR_PLUGIN_CONFIG_DIR': str(root / 'config'),
            }), patch.object(bridge.fm, '_live_agent_homes', return_value=set()), \
                    patch.object(bridge.fm, '_lease_holders', return_value={}), \
                    patch.object(bridge.fm, '_presentation_labels', return_value={}):
                self.assertEqual(endpoint_path(home, endpoint.name), str(endpoint))
                self.assertEqual(bridge.fm.real_homes(bridge.fm.discover_homes())[0].path, str(home.resolve()))
                (home / 'bin').mkdir()
                local = home / 'bin' / endpoint.name
                local.write_text('#!/bin/sh\n')
                self.assertEqual(endpoint_path(home, endpoint.name), str(local))

    @unittest.skipIf(os.name == 'nt', 'Symlink reads require POSIX/WSL')
    def test_delivery_read_through_symlinked_home_but_not_symlinked_leaf(self):
        with tempfile.TemporaryDirectory(prefix='deck symlink ') as tmp:
            root = Path(tmp)
            real = root / 'real'
            (real / 'data').mkdir(parents=True)
            (real / 'data' / 'report.md').write_text('report', encoding='utf-8')
            (real / 'data' / 'leak.md').symlink_to(real / 'data' / 'report.md')
            (root / 'link').symlink_to(real, target_is_directory=True)
            link = str(root / 'link')
            self.assertEqual(bridge.fm.delivery_read(link, link + '/data/report.md', 100), 'report')
            with self.assertRaises(OSError):
                bridge.fm.delivery_read(link, link + '/data/leak.md', 100)

    def test_safety_capability_fails_closed(self):
        with patch.object(bridge.fm, 'safe_file_reads', return_value=False):
            with self.assertRaisesRegex(OSError, 'Reading reports safely needs Linux or macOS'):
                bridge.fm.delivery_read('unused', 'data/report.md', 100)

    @unittest.skipIf(os.name == 'nt', 'Firstmate producer endpoints require POSIX/WSL')
    def test_empty_owner_reads_real_endpoint_with_legacy_environment(self):
        with tempfile.TemporaryDirectory(prefix='deck owner ') as tmp:
            root = Path(tmp)
            home = root / 'home'
            (home / 'bin').mkdir(parents=True)
            endpoint = home / 'bin/fm-bearings-snapshot.sh'
            endpoint.write_text('#!/bin/sh\nprintf \'%s\\n\' \'{"schema":"fm-bearings.v1","gates":[],"in_flight":[],"decisions_open":[],"landed":[]}\'\n')
            endpoint.chmod(0o755)
            with patch.dict(os.environ, {
                'FM_FLOW_HOMES': 'owner=' + str(home),
                'FM_FLOW_HOMES_JSON': json.dumps({'Owner': str(home)}),
                'FM_FLOW_HOMES_ONLY': '1', 'FM_ROOT_OVERRIDE': '',
                'HERDR_BIN_PATH': sys.executable,
                'HERDR_PLUGIN_CONFIG_DIR': str(root / 'config'),
            }), patch.object(bridge.fm, 'HERDR_BIN', sys.executable), \
                    patch.object(bridge.fm, '_lease_holders', return_value={}), \
                    patch.object(bridge.fm, '_presentation_labels', return_value={}):
                homes = bridge.fm.real_homes(bridge.fm.discover_homes())
                self.assertEqual(len(homes), 1)
                self.assertEqual(homes[0].path, str(home.resolve()))
                board = bridge.Board(root / 'web-data')
                board.start()
                try:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline and not board.health()['ok']:
                        time.sleep(0.05)
                    self.assertTrue(board.health()['ok'], board.health())
                    self.assertTrue(all(not c['cards'] for c in board.board()['columns']))
                finally:
                    board.stop()
                    # Collector uses an Event named _stop; do not Thread.join it.
                    time.sleep(0.1)

    @unittest.skipIf(os.name == 'nt', 'Firstmate producer endpoints require POSIX/WSL')
    def test_read_and_guarded_answer_with_separate_code_root(self):
        from fixtures import fixture
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            board, home = fixture(root)
            code = root / 'code'
            code.mkdir()
            (Path(home.path) / 'bin').rename(code / 'bin')
            # Fixture scripts explicitly locate their Python helper beside themselves.
            with patch.dict(os.environ, FM_ROOT_OVERRIDE=str(code)):
                card = next(c for c in board.collector.snapshot().cols['captains_call'])
                ticket = board.ticket(board.key(card))
                self.assertFalse(ticket['call']['error'])
                receipt = board.submit(board.key(card), {'value': 'scope', 'note': '',
                                       'occurrence': ticket['call']['occurrence'],
                                       'request_id': 'b' * 32}, 'local')
                self.assertTrue(receipt['saved'])
                self.assertTrue((Path(home.path) / 'mutations.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
