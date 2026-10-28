"""Actual raw-input TUI with two isolated homes, no Herdr/backend lifecycle."""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('outcomes', HERE / 'test_flow_landed_outcomes.py')
outcomes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(outcomes)
flow = outcomes.flow


def fixture_main(root):
    snap, parts = outcomes.fleet_fixture(root)
    from test_flow_clarity import install_explanation_endpoint
    for home, _, _ in parts:
        install_explanation_endpoint(Path(home.path))
    aggregate, fleet_cols = snap.home, snap.cols
    snap.seq = 1

    class Collector:
        _active = 'All'

        def start(self):
            pass

        def stop(self):
            pass

        def refresh_now(self):
            snap.seq += 1

        def set_active(self, label):
            self._active = label
            snap.home, snap.cols = (aggregate, fleet_cols) if label == 'All' else next(
                (home, cols) for home, cols, _ in parts if home.label == label)
            snap.seq += 1

        def snapshot(self):
            return snap

    flow.Collector = Collector
    original = flow.UI.paint

    def paint(ui, frame, width, height):
        original(ui, frame, width, height)
        with (Path(root) / 'frames.jsonl').open('a') as out:
            out.write(json.dumps({'width': width, 'height': height, 'home': snap.home.label,
                                  'text': '\n'.join(flow._ANSI_RE.sub('', row) for row in frame),
                                  'delivery': ui.delivery is not None, 'history': ui.history is not None,
                                  'scroll': ui.delivery_scroll, 'max': ui.delivery_max,
                                  'hits': ui.reader_hits, 'tabs': ui.tab_regions,
                                  'cards': [(x1, y1, x2, y2, c.bucket, c.owner_label) for x1, y1, x2, y2, c in ui.card_regions]}) + '\n')

    flow.UI.paint = paint
    return flow.main([])


class OutcomePTYTests(unittest.TestCase):
    def test_all_specific_home_summary_report_and_archive_with_real_input(self):
        proof = Path(os.environ['DECK_DELIVERY_PROOF_DIR']) if os.environ.get('DECK_DELIVERY_PROOF_DIR') else None
        for width, height in ((130, 18), (60, 16)):
            with self.subTest(width=width), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                env = dict(os.environ, FM_FLOW_SHOW_LANDED='1', PYTHONDONTWRITEBYTECODE='1',
                           HERDR_PLUGIN_STATE_DIR=str(root / 'plugin-state'), HERDR_PLUGIN_CONFIG_DIR=str(root / 'plugin-config'))
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', height, width, 0, 0))
                proc = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--fixture', directory],
                                        stdin=slave, stdout=slave, stderr=slave, env=env, cwd=str(HERE.parent))
                os.close(slave)
                raw, saved, count = bytearray(), [], 0

                def expect(predicate, label):
                    nonlocal count
                    deadline = time.monotonic() + 6
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
                                saved.append((label, frame['text']))
                                return frame
                        if proc.poll() is not None:
                            self.fail(f'TUI exited {proc.returncode}: {raw.decode(errors="replace")[-1500:]}')
                        time.sleep(.025)
                    self.fail(f'TUI timeout {label}: {raw.decode(errors="replace")[-1500:]}')

                def send(data):
                    os.write(master, data)

                def click(x, y, button=0):
                    send(f'\x1b[<{button};{x + 1};{y + 1}M'.encode())

                try:
                    board = expect(lambda f: f['home'] == 'All', 'all-board')
                    self.assertTrue(any(c[4] == 'landed' for c in board['cards']))
                    # Real keyboard Enter opens the same home-bound card from All.
                    send(b'\x1b[D' * 5 + b'\x1b[C' * 3 + b'\r')
                    summary = expect(lambda f: f['delivery'] and 'Study only; apps unchanged.' in f['text'], 'all-first-screen')
                    self.assertNotIn('Task: old', summary['text'])
                    send(b'e')
                    queued = expect(lambda f: 'Queued for Firstmate' in f['text'], 'explanation-queued')
                    x, _, y, _ = next(hit for hit in queued['hits'] if hit[3] == 'explain')
                    click(x, y)
                    expect(lambda f: 'Queued for Firstmate' in f['text'], 'explanation-deduplicated')
                    send(b'r')
                    report = expect(lambda f: 'FULL_REPORT_mate' in f['text'], 'all-keyboard-report')
                    previous = report['scroll']
                    click(5, 5, 65)
                    expect(lambda f: f['scroll'] > previous, 'all-wheel-report')
                    send(b'\x1b[H')
                    summary = expect(lambda f: f['scroll'] == 0 and f['delivery'], 'all-home')
                    x, _, y, _ = next(hit for hit in summary['hits'] if hit[3] == 'report')
                    click(x, y)
                    expect(lambda f: 'FULL_REPORT_mate' in f['text'], 'all-mouse-report')
                    send(b'\x1bH')
                    expect(lambda f: f['history'] and not f['delivery'], 'all-history')
                    send(b'old')
                    expect(lambda f: '2 matches' in f['text'], 'all-colliding-history')
                    send(b'\r')
                    expect(lambda f: f['delivery'] and 'Study only; apps unchanged.' in f['text'], 'all-archive-open')
                    send(b'\x1b\x1b')
                    board = expect(lambda f: not f['delivery'] and not f['history'], 'all-back')
                    x, _, _ = next(tab for tab in board['tabs'] if tab[2] == 'firstmate')
                    click(x, 0)
                    expect(lambda f: f['home'] == 'firstmate', 'specific-tab')
                    send(b'\x1b[D' * 5 + b'\x1b[C' * 3)
                    board = expect(lambda f: f['home'] == 'firstmate' and any(c[4] == 'landed' for c in f['cards']), 'specific-board')
                    x1, y1, _, _, _, owner = next(card for card in board['cards'] if card[4] == 'landed')
                    self.assertEqual(owner, 'firstmate')
                    click(x1 + 1, y1 + 1)
                    summary = expect(lambda f: f['delivery'] and 'Installed skill; evals pending.' in f['text'], 'specific-first-screen')
                    self.assertNotIn('Task: old', summary['text'])
                    send(b'r')
                    expect(lambda f: 'FULL_REPORT_firstmate' in f['text'], 'specific-report')
                    send(b'\x1bq')
                    proc.wait(timeout=4)
                    self.assertEqual(proc.returncode, 0)
                    marks = json.loads((root / 'plugin-state/deliveries-read.json').read_text())
                    calls = [json.loads(line) for line in (root / 'mate/calls.jsonl').read_text().splitlines()]
                    self.assertEqual(sum('--status' not in args for args in calls), 1)
                    for label in ('mate', 'firstmate'):
                        self.assertIn(flow.delivery_key(str(root / label), 'old'), marks)
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=4)
                    os.close(master)
                    if proof:
                        proof.mkdir(parents=True, exist_ok=True)
                        (proof / f'outcome-{width}x{height}.ansi').write_bytes(raw)
                        for label, text in saved:
                            (proof / f'{width}x{height}-{label}.txt').write_text(text + '\n')
                        (proof / f'outcome-{width}x{height}.json').write_text(json.dumps({'frames': len(saved), 'exit': proc.returncode,
                                                                                                    'herdr_calls': 0, 'sizes': [width, height]}))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--fixture':
        raise SystemExit(fixture_main(sys.argv[2]))
    unittest.main()
