"""Exercise the actual TUI main loop, raw keyboard, SGR mouse and ANSI paint.

Only Collector is replaced by an isolated fixture; no Herdr process is queried
or controlled. Run directly; DECK_DELIVERY_PROOF_DIR optionally keeps frames.
"""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
from pathlib import Path
import pty
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('delivery_fixture', HERE / 'test_flow_deliveries.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
flow = fixture.flow


CLOCK_EVENT = 1792878000


def fixture_board(root, timed=False):
    home = flow.Home('fixture', root, 'captain')
    snap = fixture.snapshot(home)
    snap.seq = 1
    if timed:
        payload = {
            'gates': [{'id': 'filed', 'title': 'Filed fixture task',
                       'card_time': {'kind': 'filed', 'value': '2026-10-23', 'precision': 'date', 'source': 'backlog.since'}}],
            'in_flight': [{'id': 'clocked', 'name': 'Check timestamps', 'state': 'paused', 'kind': 'ship',
                           'card_time': {'kind': 'updated', 'value': flow.datetime.fromtimestamp(CLOCK_EVENT, flow.timezone.utc).isoformat(),
                                         'precision': 'second', 'source': 'fixture.status',
                                         'original_value': str(CLOCK_EVENT), 'attempt_boundary': 'unknown',
                                         'current_attempt': 'legacy-fixture'}},
                          {'id': 'reported', 'name': 'Reported completion', 'state': 'done', 'kind': 'ship',
                           'card_time': {'kind': 'done_report', 'value': '2026-10-22', 'precision': 'date', 'source': 'fixture.report-date'}}],
            'paths': [{'id': 'clocked', 'worktree': '/tmp/fixture/clocked'}],
            'landed': [{'id': 'old', 'what': 'Completed fixture delivery', 'owner': '(main)',
                        'card_time': {'kind': 'delivered', 'value': '2026-10-21', 'precision': 'date', 'source': 'backlog.completion.done'}}],
        }
        snap.cols, snap.totals = flow.make_cards(home, payload, {}, {}, {}, [home])
        original_time_line = flow.local_time_caption

        def timed_line(event, now=None):
            clock = json.loads((Path(root) / 'clock.json').read_text())['now'] if now is None else now
            return original_time_line(event, clock)

        flow.local_time_caption = timed_line

    class FixtureCollector:
        def __init__(self):
            self.refreshes = 0

        def start(self):
            pass

        def stop(self):
            pass

        def refresh_now(self):
            self.refreshes += 1
            snap.seq += 1

        def snapshot(self):
            return snap

    flow.Collector = FixtureCollector
    original = flow.UI.paint

    def paint(ui, frame, w, h):
        original(ui, frame, w, h)
        with open(Path(root) / 'frames.jsonl', 'a') as out:
            out.write(json.dumps({'width': w, 'height': h,
                                  'text': '\n'.join(flow._ANSI_RE.sub('', ln) for ln in frame),
                                  'hits': ui.reader_hits,
                                  'seq': snap.seq, 'refreshes': ui.collector.refreshes,
                                  'details_scroll': ui.details_scroll, 'details_max': ui.details_max,
                                  'delivery_scroll': ui.delivery_scroll, 'delivery_max': ui.delivery_max,
                                  'event_epoch': snap.cols['underway'][0].event_time.epoch if timed else None,
                                  'cards': [(x1, y1, x2, y2, c.bucket) for x1, y1, x2, y2, c in ui.card_regions]}) + '\n')

    flow.UI.paint = paint
    return flow.main([])


class PTYTests(unittest.TestCase):
    def test_producer_clock_advance_details_and_history_without_backend_refresh(self):
        proof = Path(os.environ['DECK_DELIVERY_PROOF_DIR']) if os.environ.get('DECK_DELIVERY_PROOF_DIR') else None
        saved, metrics = [], []
        for width, height in ((130, 18), (60, 16)):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fixture.fixture(root)
                (root / 'clock.json').write_text(json.dumps({'now': CLOCK_EVENT + 120}))
                env = dict(os.environ, FM_FLOW_SHOW_LANDED='1', HERDR_PLUGIN_STATE_DIR=str(root / 'plugin-state'),
                           HERDR_PLUGIN_CONFIG_DIR=str(root / 'plugin-config'), PYTHONDONTWRITEBYTECODE='1')
                env.pop('CD_WEB_LANGUAGE', None)
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', height, width, 0, 0))
                proc = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--fixture-time-board', directory],
                                        stdin=slave, stdout=slave, stderr=slave, env=env, cwd=ROOT_PATH)
                os.close(slave)
                raw, count = bytearray(), 0

                def expect(predicate, label):
                    nonlocal count
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        while select.select([master], [], [], 0)[0]:
                            try:
                                data = os.read(master, 65536)
                            except OSError:
                                break
                            if not data:
                                break
                            raw.extend(data)
                        try:
                            frames = [json.loads(line) for line in (root / 'frames.jsonl').read_text().splitlines()]
                        except (OSError, ValueError):
                            frames = []
                        for index in range(count, len(frames)):
                            frame = frames[index]
                            if predicate(frame):
                                count = index + 1
                                self.assertEqual(len(frame['text'].splitlines()), height)
                                self.assertTrue(all(flow.display_width(line) <= width for line in frame['text'].splitlines()))
                                self.assertEqual(frame['event_epoch'], CLOCK_EVENT)
                                self.assertEqual(frame['refreshes'], 0)
                                self.assertEqual(frame['seq'], 1)
                                saved.append((f'clock-{width}x{height}-{label}', frame['text']))
                                return frame
                        if proc.poll() is not None:
                            self.fail(f'Clock TUI exited {proc.returncode}: {raw.decode(errors="replace")[-1500:]}')
                        time.sleep(.025)
                    self.fail(f'Clock TUI timeout: {label}; {raw.decode(errors="replace")[-1500:]}')

                def send(data):
                    os.write(master, data)

                try:
                    expect(lambda f: bool(f['cards']), 'initial-board')
                    send(b'\x1b[D' * 5 + b'\x1b[C')
                    age = '2 min'
                    first = expect(lambda f: age in f['text'], 'two-minutes')
                    self.assertIn('? help', first['text'])
                    self.assertIn('d details', first['text'])
                    self.assertIn('Technical hold', first['text'])
                    (root / 'clock-next.json').write_text(json.dumps({'now': CLOCK_EVENT + 180}))
                    (root / 'clock-next.json').replace(root / 'clock.json')
                    age = '3 min'
                    advanced = expect(lambda f: age in f['text'], 'three-minutes-no-input')
                    send(b'd')
                    detail = expect(lambda f: 'Details · read only' in f['text'], 'details')
                    seen = detail['text']
                    while detail['details_scroll'] < detail['details_max']:
                        previous = detail['details_scroll']
                        send(b'\x1b[6~')
                        detail = expect(lambda f: f['details_scroll'] > previous, f'detail-page-{previous}')
                        seen += '\n' + detail['text']
                    for value in ('fixture.status', 'unknown', 'context', 'Firstmate', 'UTC'):
                        self.assertIn(value, seen)
                    send(b'\x1b')
                    expect(lambda f: age in f['text'] and 'Details · read only' not in f['text'], 'back-to-board')
                    send(b'\x1bH')
                    expect(lambda f: 'Delivery history' in f['text'], 'history')
                    send(b'history-29')
                    expect(lambda f: 'history-29' in f['text'] and '1 matches' in f['text'], 'archive-search')
                    send(b'\r')
                    delivery = expect(lambda f: 'Delivery ·' in f['text'], 'archive-open')
                    self.assertIn('[Back]', delivery['text'])
                    retained = delivery['text']
                    while delivery['delivery_scroll'] < delivery['delivery_max']:
                        previous = delivery['delivery_scroll']
                        send(b'\x1b[6~')
                        delivery = expect(lambda f: f['delivery_scroll'] > previous, f'archive-page-{previous}')
                        retained += '\n' + delivery['text']
                    self.assertIn('Historical delivery 29', retained)
                    send(b'd')
                    expect(lambda f: 'Details and sources' in f['text'], 'archive-details')
                    send(b'\x1b\x1bq')
                    proc.wait(timeout=4)
                    self.assertEqual(proc.returncode, 0)
                    metrics.append({'width': width, 'height': height, 'event_epoch': CLOCK_EVENT,
                                    'clock_advanced': 60, 'refreshes': 0, 'seq': 1, 'result': 'pass'})
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=4)
                    os.close(master)
                    if proof:
                        proof.mkdir(parents=True, exist_ok=True)
                        (proof / f'clock-pty-{width}x{height}.ansi').write_bytes(raw)
        if proof:
            for name, text in saved:
                (proof / (name + '.txt')).write_text(text + '\n')
            (proof / 'clock-pty-summary.json').write_text(json.dumps({'sizes': metrics, 'frames': len(saved),
                                                                    'herdr_calls': 0, 'result': 'pass'}, indent=2))

    def test_actual_tui_keyboard_click_scroll_archive_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = fixture.fixture(root)
            env = dict(os.environ, FM_FLOW_SHOW_LANDED='1',
                       HERDR_PLUGIN_STATE_DIR=str(root / 'plugin-state'),
                       HERDR_PLUGIN_CONFIG_DIR=str(root / 'plugin-config'),
                       PYTHONDONTWRITEBYTECODE='1')
            env.pop('CD_WEB_LANGUAGE', None)
            proof = Path(os.environ['DECK_DELIVERY_PROOF_DIR']) if os.environ.get('DECK_DELIVERY_PROOF_DIR') else None
            saved = []
            for width, height in ((130, 15), (60, 12)):
                frames_path = root / 'frames.jsonl'
                frames_path.write_text('')
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', height, width, 0, 0))
                proc = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--fixture-board', directory],
                                        stdin=slave, stdout=slave, stderr=slave, env=env, cwd=ROOT_PATH)
                os.close(slave)
                raw = bytearray()
                count = 0

                def drain():
                    while select.select([master], [], [], 0)[0]:
                        try:
                            data = os.read(master, 65536)
                        except OSError:
                            break
                        if not data:
                            break
                        raw.extend(data)

                def expect(predicate, label):
                    nonlocal count
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        drain()
                        try:
                            frames = [json.loads(line) for line in frames_path.read_text().splitlines()]
                        except (ValueError, FileNotFoundError):
                            frames = []
                        for index in range(count, len(frames)):
                            frame = frames[index]
                            if predicate(frame):
                                count = index + 1
                                self.assertEqual(len(frame['text'].splitlines()), height)
                                self.assertTrue(all(flow.display_width(line) <= width for line in frame['text'].splitlines()))
                                saved.append((f'{width}x{height}-{label}', frame['text']))
                                return frame
                        if proc.poll() is not None:
                            self.fail(f'TUI exited {proc.returncode}: {raw.decode(errors="replace")[-2000:]}')
                        time.sleep(.025)
                    self.fail(f'TUI timeout: {label}; {raw.decode(errors="replace")[-1000:]}')

                def send(data):
                    os.write(master, data)

                def click(x, y, button=0):
                    send(f'\x1b[<{button};{x + 1};{y + 1}M'.encode())

                try:
                    expect(lambda f: bool(f['cards']), 'initial-board')
                    send(b'\x1b[D' * 5 + b'\x1b[C' * 3)
                    board = expect(lambda f: any(r[-1] == 'landed' for r in f['cards']), 'board')
                    self.assertIn('new · Delivery' if width == 130 else 'read', board['text'])
                    # Mouse opens Landed directly; no pane focus command exists
                    # in this process or the fixture collector.
                    x1, y1, _, _, _ = next(r for r in board['cards'] if r[-1] == 'landed')
                    click(x1 + 1, y1 + 1)
                    summary = expect(lambda f: 'Delivery ·' in f['text'], 'delivery-click')
                    self.assertIn('Only the skill authoring', summary['text'])
                    send(b'r')
                    delivery = expect(lambda f: 'Full report · data/old/report.md' in f['text'], 'full-report')
                    retained = delivery['text']
                    while delivery['delivery_scroll'] < delivery['delivery_max']:
                        previous = delivery['delivery_scroll']
                        send(b'\x1b[6~')
                        delivery = expect(lambda f: f['delivery_scroll'] > previous, f'delivery-page-{previous}')
                        retained += '\n' + delivery['text']
                    self.assertIn('END_OF_REPORT', retained)
                    send(b'd')
                    delivery = expect(lambda f: 'Details and sources' in f['text'], 'delivery-details')
                    retained = delivery['text']
                    while delivery['delivery_scroll'] < delivery['delivery_max']:
                        previous = delivery['delivery_scroll']
                        send(b'\x1b[6~')
                        delivery = expect(lambda f: f['delivery_scroll'] > previous, f'details-page-{previous}')
                        retained += '\n' + delivery['text']
                    self.assertIn('RECEIPT_END', retained)
                    click(5, 5, 64)
                    expect(lambda f: f['delivery_scroll'] < delivery['delivery_scroll'], 'wheel-up')
                    send(b'\x1bH')
                    expect(lambda f: 'Delivery history' in f['text'], 'history')
                    send(b'history-29')
                    expect(lambda f: 'history-29' in f['text'] and '1 matches' in f['text'], 'archive-search')
                    send(b'\r')
                    expect(lambda f: 'Delivery ·' in f['text'], 'archive-enter')
                    send(b'\x1b\x15old\r')
                    expect(lambda f: 'Only the skill authoring' in f['text'], 'recap-enter')
                    send(b'r')
                    expect(lambda f: 'Full report · data/old/report.md' in f['text'], 'report-and-receipt')
                    send(b'\x1b\t')
                    expect(lambda f: 'Retained delivery history' in f['text'], 'source-info')
                    send(b'\x1b\x1bq')
                    proc.wait(timeout=4)
                    drain()
                    self.assertEqual(proc.returncode, 0)
                    self.assertIn(b'\x1b[?1006h', raw)
                    self.assertIn(b'\x1b[?1006l', raw)
                    ledger = json.loads((root / 'plugin-state/deliveries-read.json').read_text())
                    self.assertIn(flow.delivery_key(home.path, 'old'), ledger)
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=4)
                    os.close(master)
                if proof:
                    proof.mkdir(parents=True, exist_ok=True)
                    (proof / f'pty-{width}x{height}.ansi').write_bytes(raw)
            if proof:
                for name, text in saved:
                    (proof / (name + '.txt')).write_text(text + '\n')
                (proof / 'pty-summary.json').write_text(json.dumps({'sizes': [[130, 15], [60, 12]], 'frames': len(saved), 'result': 'pass', 'herdr_calls': 0}, indent=2))


ROOT_PATH = str(HERE.parent)
if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--fixture-board':
        raise SystemExit(fixture_board(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == '--fixture-time-board':
        raise SystemExit(fixture_board(sys.argv[2], timed=True))
    unittest.main()
