#!/usr/bin/env python3
"""Native foreground/managed launcher. Controls only its authenticated instance."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import secrets
import socket
import socketserver
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from deck_platform import file_lock, private_directory

CONFIG_KEYS = {
    'CD_WEB_PORT', 'CD_WEB_BIND', 'CD_WEB_ORIGIN', 'CD_WEB_USERS',
    'CD_WEB_HERDR_ORIGIN', 'CD_WEB_DATA', 'CD_WEB_RUNTIME',
    'FM_HOME', 'FM_ROOT_OVERRIDE', 'FM_BASH', 'FM_FLOW_HOMES', 'FM_FLOW_HOMES_JSON',
    'FM_FLOW_HOMES_ONLY', 'FM_FLOW_BEARINGS_SECS', 'FM_FLOW_BEARINGS_TIMEOUT',
    'HERDR_BIN_PATH', 'HERDR_PLUGIN_CONFIG_DIR', 'HERDR_PLUGIN_STATE_DIR',
}


def configure(path):
    if not path:
        return
    data = json.loads(Path(path).expanduser().read_text(encoding='utf-8'))
    if not isinstance(data, dict) or set(data) - CONFIG_KEYS:
        raise ValueError('config must contain only documented Deck environment keys')
    if any(not isinstance(v, str) for v in data.values()):
        raise ValueError('config values must be strings (FM_FLOW_HOMES_JSON is JSON encoded)')
    # Explicit configuration overrides inherited environment, like a service file.
    os.environ.update(data)


def request(directory, command):
    record = json.loads((directory / 'control.json').read_text(encoding='utf-8'))
    with socket.create_connection(('127.0.0.1', record['port']), timeout=2) as sock:
        sock.sendall((json.dumps({'token': record['token'], 'command': command}) + '\n').encode())
        with sock.makefile('rb') as stream:
            result = json.loads(stream.readline(4096))
    if result.get('instance') != record['token']:
        raise ValueError('control instance changed; no process was signalled')
    result.pop('instance')
    return result


def managed(directory, port=None):
    import bridge
    stop = threading.Event()
    token = secrets.token_hex(32)
    state = {'state': 'starting', 'pid': os.getpid()}

    class Control(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(2)
            try:
                data = json.loads(self.rfile.readline(4096))
                if not secrets.compare_digest(str(data.get('token', '')), token):
                    return
                if data.get('command') == 'stop':
                    stop.set()
                elif data.get('command') != 'status':
                    return
                self.wfile.write((json.dumps(dict(state, instance=token)) + '\n').encode())
            except (OSError, ValueError):
                return

    class ControlServer(socketserver.ThreadingTCPServer):
        daemon_threads = True

    with ControlServer(('127.0.0.1', 0), Control) as control:
        record = directory / 'control.json'
        temp = directory / ('control-' + token + '.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'port': control.server_address[1], 'token': token}, stream)
        os.replace(temp, record)
        thread = threading.Thread(target=control.serve_forever, daemon=True)
        thread.start()
        try:
            argv = ['--port', str(port)] if port is not None else []
            return bridge.main(argv, stop_event=stop,
                               on_ready=lambda: state.update(state='running'))
        finally:
            control.shutdown()
            thread.join(timeout=3)
            record.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run', 'managed', 'start', 'status', 'stop'))
    parser.add_argument('--config', help='private JSON environment file, outside the checkout')
    parser.add_argument('--port', type=int)
    args = parser.parse_args(argv)
    configure(args.config)
    if args.command == 'run':
        import bridge
        return bridge.main(['--port', str(args.port)] if args.port is not None else [])
    directory = Path(os.environ.get('CD_WEB_RUNTIME') or private_directory('runtime')).expanduser()
    if args.command in ('status', 'stop'):
        try:
            result = request(directory, args.command)
        except (OSError, ValueError, KeyError):
            print('No authenticated managed instance found; no process was signalled.')
            return 1
        if args.command == 'stop':
            for _ in range(100):
                try:
                    request(directory, 'status')
                except (OSError, ValueError, KeyError):
                    print('Managed instance stopped.')
                    return 0
                time.sleep(0.1)
            print('Stop requested; shutdown has not completed.')
            return 1
        print(json.dumps(result))
        return 0
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        raise ValueError('runtime directory must not be a symlink')
    if args.command == 'start':
        try:
            current = request(directory, 'status')
            if current.get('state') == 'running':
                print(json.dumps(current))
                return 0
        except (OSError, ValueError, KeyError):
            pass
        command = [sys.executable, str(Path(__file__).resolve()), 'managed']
        if args.port is not None:
            command += ['--port', str(args.port)]
        kwargs = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS} if os.name == 'nt' else {'start_new_session': True}
        fd = os.open(directory / 'server.log', os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'ab') as log:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **kwargs)
        for _ in range(100):
            try:
                result = request(directory, 'status')
                if result.get('state') == 'running':
                    print(json.dumps(result))
                    return 0
            except (OSError, ValueError, KeyError):
                pass
            if child.poll() is not None:
                break
            time.sleep(0.1)
        print('Startup not confirmed; inspect the private server.log.')
        return 1
    # Match the legacy run-managed.sh lock path and flock semantics on POSIX.
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(file_lock(directory / 'server.lock', blocking=False))
        except (BlockingIOError, PermissionError):
            return 0
        return managed(directory, args.port)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
