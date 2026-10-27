#!/usr/bin/env python3
"""Private, dependency-free HTTP projection of the Deck's existing model.

No backlog, supervisor, shell-command API, notification sender or model calls.
The single collector always reads All; selecting a crew never changes another
browser's view. Mutations go only through the owning home's public intakes.
"""
from __future__ import annotations

import argparse
import hashlib
import http.cookies
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'scripts'))
from localization import Localization
from presentation import excerpt, project
import flow_tui as fm  # noqa: E402
from deck_platform import endpoint_path, private_directory, safe_file_reads, shell_command

STATIC = HERE / 'static'
COLUMNS = [('charted', 'Up next'), ('underway', 'Underway'),
           ('captains_call', 'Your decision'), ('landed', 'Delivered')]
MAX_BODY = 16384


class Refusal(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def digest(*values):
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def home_id(home):
    return hashlib.sha256(fm.delivery_home_identity(home).encode('utf-8')).hexdigest()[:32]


def event_json(event, translate=None):
    if not isinstance(event, fm.CardTime):
        event = fm.CardTime('Updated')
    return {'label': 'Last report' if event.label == 'Updated' else event.label, 'epoch': event.epoch,
            'date': event.day.isoformat() if event.day else None,
            'source': event.source, 'problem': event.problem,
            'detail': fm.card_time_detail(event, translate=translate)}


def human_title(value, translate=lambda text: text):
    # Canonical titles sometimes retain a report link as a trailing token.
    # Keep the original in Details; a filesystem location is not the headline.
    text = fm.readable_markdown(str(value or ''))
    text = re.sub(r'(?<!\S)(?:data/|/[^\s]*/data/)[^\s]+\.(?:md|json|txt)(?=\s|$)', '', text)
    return re.sub(r'\s+', ' ', text).strip(' ·–-') or translate('Task without a readable title')


class Collector(fm.Collector):
    """Track source-read failures separately from cheap card repaint times."""
    def __init__(self):
        super().__init__(fm.ALL_CREW_LABEL)
        # The TUI's first snapshot defaults to empty. Web clients can poll
        # before discovery/Herdr/bearings finish; that is loading, not empty.
        self._snap.loading = True
        self.source_errors = {}

    def _load_bearings(self, home, now, agents, panes, homes):
        stale = self._bearings_stale(home, now)
        before = self._raw.get(home.label, ({}, 0, ''))[1]
        result = super()._load_bearings(home, now, agents, panes, homes)
        after = self._raw.get(home.label, ({}, 0, ''))[1]
        if stale:
            self.source_errors[home.label] = '' if after > before else 'Could not refresh Firstmate.'
        return result


class Board:
    def __init__(self, data_dir, herdr_origin='', collector=None, language=None):
        self.i18n = Localization(language if language is not None else os.environ.get('CD_WEB_LANGUAGE', 'en'))
        self.t = self.i18n.t
        self.collector = collector or Collector()
        self.data_dir = Path(data_dir).expanduser().absolute()
        self.herdr_origin = herdr_origin
        self.mutation_lock = threading.Lock()
        self.history_lock = threading.Lock()
        self.history_cache = {}

    def event_json(self, event):
        return event_json(event, self.t)

    def start(self):
        self.collector.start()

    def stop(self):
        self.collector.stop()

    def homes(self):
        return fm.real_homes(self.collector.snapshot().homes)

    def health(self):
        snap = self.collector.snapshot()
        ready = bool(snap.collected_at and fm.real_homes(snap.homes))
        return {'ok': ready and not snap.loading and not snap.error,
                'collector_at': snap.collected_at, 'loading': bool(snap.loading),
                'error': snap.error}

    def home(self, identity):
        matches = [h for h in self.homes() if home_id(h) == identity]
        if len(matches) != 1:
            raise Refusal('The home is unavailable or its identity is ambiguous.', 404)
        return matches[0]

    def owner(self, card):
        homes = self.homes()
        if card.bucket == 'landed':
            identity = getattr(card, 'delivery_identity', '')
            matches = [h for h in homes if fm.delivery_home_identity(h) == identity]
        elif card.owner and card.owner != '(main)':
            matches = [h for h in homes if card.owner in (h.label, h.mate_id)]
        else:
            matches = [h for h in homes if h.path == card.home_path]
        if len(matches) != 1:
            raise Refusal('The owner of this task is unconfirmed.')
        return matches[0]

    def key(self, card):
        try:
            identity = home_id(self.owner(card))
        except Refusal:
            identity = digest(card.home_path, card.owner)
        return digest(identity, card.task, card.bucket)[:40]

    def card(self, key):
        matches = [c for column, _ in COLUMNS for c in self.collector.snapshot().cols.get(column, [])
                   if self.key(c) == key]
        if len(matches) != 1:
            raise Refusal('This task changed or is no longer in view. Refresh the board.', 404)
        return matches[0]

    def card_json(self, card):
        owner = None
        try:
            owner = self.owner(card)
        except Refusal:
            pass
        context = fm.flow_context(card, translate=self.t)
        report_state, report_text, report_time = fm.durable_report(card)
        pane = str(getattr(card, 'pane_id', '') or '')
        # The installed Herdr Web supports ?machine=local&pane=…&view=chat.
        # Remote machine mapping is deliberately not guessed from a route label.
        link = ''
        if self.herdr_origin and owner and not fm.is_remote_home(owner) and pane:
            link = self.herdr_origin + '/?' + urlencode({'machine': 'local', 'pane': pane, 'view': 'chat'})
        return {'key': self.key(card), 'task': card.task, 'title': human_title(card.title, self.t),
                'original_title': card.title,
                'bucket': card.bucket, 'owner': owner.label if owner else self.t('Unconfirmed owner'),
                'home': home_id(owner) if owner else '', 'context': context,
                'presentation': project(card, context),
                'activity': fm.card_activity(card),
                'report': {'state': report_state, 'text': report_text, 'time': self.event_json(report_time)},
                'native': {'state': getattr(card, 'snapshot_state', '') or 'unknown',
                           'detail': getattr(card, 'doing', '') or self.t('No observation recorded.'),
                           'herdr': getattr(card, 'live_status', '') or 'unknown'},
                'dependencies': getattr(card, 'dependencies', []) or [],
                'supervisor': getattr(card, 'supervisor', {}) or {},
                'time': self.event_json(getattr(card, 'event_time', None)),
                'summary': excerpt(getattr(card, 'delivery_summary', '') or ''),
                'answered': bool(getattr(card, 'answered_hidden', False)),
                'agent': fm.UI.card_agent_line(card), 'herdr_url': link}

    def board(self, selected=''):
        snap = self.collector.snapshot()
        chosen = self.home(selected) if selected else None
        homes = self.homes()
        columns = []
        for key, title in COLUMNS:
            cards = [self.card_json(c) for c in snap.cols.get(key, [])]
            if chosen:
                cards = [c for c in cards if c['home'] == selected]
            columns.append({'id': key, 'title': self.t(title), 'cards': cards})
        sources = []
        for h in homes:
            if chosen and home_id(h) != selected:
                continue
            raw, received, _ = self.collector._raw.get(h.label, ({}, 0, ''))
            error = getattr(self.collector, 'source_errors', {}).get(h.label, '')
            sources.append({'home': home_id(h), 'label': h.label,
                            'received_at': received, 'generated': raw.get('generated', ''),
                            'error': self.t(error), 'remote': fm.is_remote_home(h),
                            'supervisor': raw.get('supervisor') or {}})
        return {'seq': snap.seq, 'collected_at': snap.collected_at,
                'server_at': time.time(), 'loading': bool(snap.loading),
                'error': self.t(snap.error), 'sources': sources,
                'homes': [{'id': home_id(h), 'label': h.label} for h in homes],
                'columns': columns, 'home': selected,
                'source_ttl': fm._bearings_ttl(),
                'retention': self.t('Recent is a limited view. History searches retained deliveries only; it cannot recover deleted records.')}

    def receipts(self, actor):
        directory = self.data_dir / 'answers'
        if not directory.exists():
            return []
        if directory.is_symlink():
            raise Refusal('Unsafe receipt directory.')
        result = []
        for path in sorted(directory.glob('*.json'))[:1000]:
            if not re.fullmatch(r'[a-f0-9]{64}\.json', path.name):
                continue
            try:
                data = json.loads(fm.delivery_read(str(self.data_dir), str(path), 65536))
                if data.get('actor') == actor:
                    result.append({k: v for k, v in data.items() if k not in ('actor', 'payload_hash')})
            except (OSError, ValueError):
                continue
        return sorted((self.present_receipt(r) for r in result), key=lambda r: r.get('at', 0), reverse=True)[:10]

    def resolve(self, identity, task, view, actor, item=''):
        if not fm.KEY_RE.fullmatch(task) or view not in ('call', 'delivery'):
            raise Refusal('Invalid task link.', 400)
        home = self.home(identity)
        if view == 'delivery':
            try:
                return {'view': 'delivery', 'delivery': self.delivery(identity, task),
                        'notice': self.t('The delivery is identified by home and task. History cannot verify the original notification identifier.') if item else ''}
            except Refusal as exc:
                return {'view': 'unavailable', 'task': task, 'owner': home.label, 'message': self.t(str(exc)), 'receipts': []}
        matches = []
        for card in self.collector.snapshot().cols.get('captains_call', []):
            try:
                if card.task == task and home_id(self.owner(card)) == identity:
                    matches.append(card)
            except Refusal:
                continue
        message = 'This task is no longer among current questions. It may have been resolved, reopened or changed state; this link does not prove which. Check the board or its receipts.'
        current_key = self.key(matches[0]) if len(matches) == 1 else ''
        occurrence = re.fullmatch(r'hold:([1-9][0-9]{0,8})', item)
        if current_key:
            if not item:
                return {'view': 'call', 'key': current_key}
            try:
                current = self.call_identity(home, task)
                ordinal = int(current.rsplit('#', 1)[1]) + 1
                if occurrence and int(occurrence[1]) == ordinal:
                    return {'view': 'call', 'key': current_key}
                message = ('The notification refers to an earlier question or an unverifiable identity. '
                           'The current question is not presented as the notification’s question. You can explicitly open it for review.')
            except Refusal:
                pass
        if occurrence and not fm.is_remote_home(home):
            try:
                result = subprocess.run(shell_command(endpoint_path(home.path, 'fm-captain-hold.sh'),
                                         'resolved', task, occurrence[1]), env=self.child_env(home),
                                        capture_output=True, text=True, timeout=15,
                                        stdin=subprocess.DEVNULL)
                if result.returncode == 0:
                    message = 'The question from this notification is resolved according to Firstmate’s canonical intake. This link does not resend an answer.'
            except (OSError, subprocess.SubprocessError):
                pass
        return {'view': 'unavailable', 'task': task, 'owner': home.label,
                'message': self.t(message), 'current_key': current_key,
                'receipts': [r for r in self.receipts(actor) if r.get('home') == identity and r.get('task') == task]}

    def records(self, home, force=False):
        identity = home_id(home)
        with self.history_lock:
            rows, note, at = self.history_cache.get(identity, ([], '', 0))
            if force or time.time() - at > 15:
                rows, note = fm.delivery_records(home, translate=self.t)
                self.history_cache[identity] = rows, note, time.time()
            return rows, note

    def history(self, selected='', query=''):
        homes = [self.home(selected)] if selected else self.homes()
        result, details = [], []
        for home in homes:
            rows, note = self.records(home)
            details.append({'home': home.label, 'source': note})
            for row in rows:
                if query.casefold() not in ' '.join(str(row.get(k) or '') for k in
                                                   ('id', 'title', 'body', 'artifact')).casefold():
                    continue
                event = fm.producer_card_time(row.get('card_time'), 'landed',
                                              fm.CardTime('Delivered', row.get('closed')))
                result.append({'task': row['id'], 'title': human_title(row.get('title') or row['id'], self.t),
                               'home': home_id(home), 'owner': home.label,
                               'time': self.event_json(event), 'closed': row.get('closed') or ''})
        result.sort(key=lambda row: (row['closed'], row['task'], row['home']), reverse=True)
        return {'records': result[:1000], 'omitted': max(0, len(result) - 1000),
                'details': details, 'scope': self.t('Deliveries retained by Firstmate. Recent has a separate limit; searching does not change retention.')}

    def delivery(self, identity, task):
        home = self.home(identity)
        if not fm.KEY_RE.fullmatch(task):
            raise Refusal('Invalid task identity.', 400)
        rows, note = self.records(home, force=True)
        matches = [r for r in rows if r['id'] == task]
        if len(matches) != 1:
            raise Refusal('This delivery is no longer retained or the task was reopened.', 404)
        record = matches[0]
        content = fm.delivery_content(record, home, split_views=True, translate=self.t, report_heading=False)
        if not isinstance(content, dict):
            content = {'summary': self.t('No summary is available for this delivery.'), 'report': '', 'details': content}
        return {'task': task, 'home': identity, 'owner': home.label,
                'title': human_title(record.get('title') or task, self.t), **content, 'sources': note,
                'time': self.event_json(fm.producer_card_time(record.get('card_time'), 'landed',
                                                       fm.CardTime('Delivered', record.get('closed'))))}

    def explanation(self, identity, task, status=True):
        home = self.home(identity)
        if not fm.KEY_RE.fullmatch(task):
            raise Refusal('Invalid task identity.', 400)
        rows, _ = self.records(home, force=not status)
        if len([row for row in rows if row['id'] == task]) != 1:
            raise Refusal('This delivery is no longer retained or the task was reopened.', 404)
        return fm.explanation_request(home, task, status=status, translate=self.t)

    def call_identity(self, home, task):
        if fm.is_remote_home(home) or not fm.KEY_RE.fullmatch(task):
            raise Refusal('This home does not offer verifiable web answers.')
        script = Path(endpoint_path(home.path, 'fm-captain-hold.sh'))
        try:
            result = subprocess.run(shell_command(script, 'open', task, '--identity'),
                                    env=self.child_env(home), capture_output=True, text=True,
                                    stdin=subprocess.DEVNULL, timeout=15)
        except (OSError, subprocess.SubprocessError):
            raise Refusal('Could not check the current question. Your draft is preserved.')
        identity = result.stdout.strip()
        if result.returncode != 0 or not re.fullmatch(r'[^\s#]+#\d+', identity):
            raise Refusal('The question is no longer open or its occurrence cannot be verified.')
        return identity

    @staticmethod
    def child_env(home):
        env = fm._child_env()
        # External intake, never impersonate Root or inherit a worker namespace.
        for key in list(env):
            if key.startswith('FM_') or key in ('CODEX_THREAD_ID', 'CODEX_SESSION_ID', 'CLAUDE_SESSION_ID',
                                               'CLAUDE_CODE_SESSION_ID', 'CLAUDE_PID',
                                               'HERDR_PANE_ID', 'HERDR_TAB_ID', 'HERDR_WORKSPACE_ID'):
                env.pop(key, None)
        env['FM_HOME'] = home.path
        return env

    def content(self, card, home):
        # The confirmed owning home is the active context, never aggregate All.
        content = fm.decision_card_content(card, self.homes(), home)
        if os.path.realpath(content['home_path']) != fm.delivery_home_identity(home):
            raise Refusal('The intake does not match the owning home.')
        context = fm.flow_context(card)
        if context['phase'] == 'human' and not content.get('about'):
            content['about'] = context['reason']
        return content

    def ticket(self, key):
        card = self.card(key)
        result = self.card_json(card)
        if card.bucket != 'captains_call':
            return result
        home = self.owner(card)
        content = self.content(card, home)
        try:
            identity = self.call_identity(home, card.task)
            guarded = self.guarded_intake(home)
            error = '' if guarded else 'This home’s intake needs an occurrence guard for web answers.'
        except Refusal as exc:
            identity, error = '', str(exc)
        result['call'] = {k: content[k] for k in ('title', 'about', 'decide', 'detail', 'options',
                                                   'recommend', 'freeform', 'freeform_hint', 'close')}
        result['call']['identity'] = identity
        result['call']['occurrence'] = digest(home_id(home), card.task, identity, content['stamp'],
                                            fm.flow_context(card)['reason'], result['call'])
        result['call']['error'] = error
        display = fm.decision_card_content(card, self.homes(), home, translate=self.t)
        if not display.get('about') and result['context']['phase'] == 'human':
            display['about'] = result['context']['reason']
        result['call']['presentation'] = {k: display[k] for k in ('about', 'decide', 'freeform_hint', 'options')}
        result['call']['error'] = self.t(error)
        result['call']['answer_limit'] = min(fm.ANSWER_LIMIT, 512)
        return result

    @staticmethod
    def guarded_intake(home):
        try:
            response = subprocess.run(shell_command(endpoint_path(home.path, 'fm-captain-hold.sh'),
                                       'capabilities', '--json'), env=Board.child_env(home),
                                      capture_output=True, text=True, timeout=10,
                                      stdin=subprocess.DEVNULL)
            data = json.loads(response.stdout)
            return (response.returncode == 0 and data.get('schema') == 'fm-captain-hold.capabilities.v1'
                    and data.get('guarded_input') == 'single-row' and
                    all(command in data.get('expected_identity', []) for command in ('answers', 'reconcile-requests')))
        except (OSError, ValueError, subprocess.SubprocessError):
            return False

    def submit(self, key, body, actor):
        if not all(isinstance(body.get(k, ''), str) for k in ('value', 'note', 'occurrence', 'request_id')):
            raise Refusal('Invalid answer format.', 400)
        request_id = body.get('request_id', '')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{16,80}', request_id):
            raise Refusal('Missing submission identifier.', 400)
        occurrence = body.get('occurrence', '')
        if not re.fullmatch(r'[a-f0-9]{64}', occurrence):
            raise Refusal('Missing question occurrence.', 400)
        # One immutable receipt per occurrence, across clients and server restarts.
        # Lock is held across validation/intake; Firstmate additionally owns CAS.
        if not safe_file_reads():
            raise Refusal('The secure answer backend requires POSIX; run Deck and Firstmate in WSL.', 503)
        with self.mutation_lock:
            receipt_dir = self.data_dir / 'answers'
            if receipt_dir.is_symlink():
                raise Refusal('Unsafe receipt directory.')
            path = receipt_dir / (digest(key, occurrence) + '.json')
            payload_hash = digest(key, occurrence, body.get('value', ''), body.get('note', ''))
            if path.exists() or path.is_symlink():
                try:
                    previous = json.loads(fm.delivery_read(str(self.data_dir), str(path), 65536))
                except (OSError, ValueError):
                    raise Refusal('Unverifiable receipt; Firstmate must review this submission.')
                if previous.get('payload_hash') != payload_hash or previous.get('actor') != actor:
                    raise Refusal('This occurrence already has another submission. Your draft is preserved.')
                return self.present_receipt(dict(previous, replay=True))
            card = self.card(key)
            if card.bucket != 'captains_call':
                raise Refusal('The task is no longer waiting for a human answer.')
            home = self.owner(card)
            ticket = self.ticket(key)
            call = ticket['call']
            if call['error']:
                raise Refusal(call['error'])
            if not secrets.compare_digest(call['occurrence'], occurrence):
                raise Refusal('The question changed. Review the current version; your draft is preserved.')
            value, note = body.get('value', '').strip(), body.get('note', '').strip()
            # The installed keyed intake bounds each field to 512 bytes. Reject
            # rather than silently truncate the captain's words.
            if any(ch in value + note for ch in ('\x00', '\t', '\n', '\r')):
                raise Refusal('The answer must be a single line; it has not been sent.', 400)
            if len(note.encode()) > min(fm.ANSWER_LIMIT, 512):
                raise Refusal('The answer exceeds 512 bytes; shorten it before sending.', 400)
            options = call['options']
            if value and value not in [o['value'] for o in options]:
                raise Refusal('That option is no longer available.')
            if (not value and (not call['freeform'] or not note)):
                raise Refusal('Choose an option or write an answer.', 400)
            content = self.content(card, home)
            dialog = fm.DecisionDialog(content)
            dialog.selected = next((i for i, opt in enumerate(options) if opt['value'] == value), -1)
            dialog.note = note
            line = dialog.reconcile_line() if value == 'reconcile' else dialog.keyed_line()
            if value != 'reconcile':
                fields = line.split('\t')
                # The optional label is context, not the captain's answer.
                # Preserve every answer byte even with an unusually long title.
                fields[2] = fm._flatten_field(card.title)[:80]
                line = '\t'.join(fields)
            if any(len(field.encode()) > 512 for field in line.split('\t')):
                raise Refusal('The option and text together exceed the intake limit.', 400)
            # No write until all checks pass. The pending marker is deliberately
            # durable: after an interrupted call retry cannot submit twice.
            receipt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            receipt = {'request_id': request_id, 'payload_hash': payload_hash, 'actor': actor,
                       'home': home_id(home), 'task': card.task, 'title': card.title, 'occurrence': occurrence,
                       'at': time.time(), 'saved': None, 'notice': 'unknown',
                       'ack': 'unknown', 'consumed': 'unknown', 'state': 'uncertain',
                       'message': 'Submission started. If the connection drops, check this receipt before retrying.'}
            self.store_receipt(path, receipt, exclusive=True)
            try:
                args = ['reconcile-requests', '--source-id', fm.DECISION_SOURCE_ID,
                        '--source', fm.DECISION_SOURCE] if value == 'reconcile' else [
                            'answers', '--source', fm.DECISION_SOURCE]
                if value == 'reconcile':
                    script = endpoint_path(home.path, 'fm-captain-hold.sh')
                    binding = subprocess.run(shell_command(script, 'binding', fm.DECISION_SOURCE_ID),
                                             env=self.child_env(home), capture_output=True, text=True,
                                             stdin=subprocess.DEVNULL, timeout=15)
                    if binding.returncode or not binding.stdout.strip():
                        binding = subprocess.run(shell_command(script, 'bind', fm.DECISION_SOURCE_ID),
                                                 env=self.child_env(home), capture_output=True, text=True,
                                                 stdin=subprocess.DEVNULL, timeout=15)
                        if binding.returncode:
                            raise Refusal('Could not bind the review intake.')
                args += ['--expected-identity', call['identity']]
                process = subprocess.run(shell_command(endpoint_path(home.path, 'fm-captain-hold.sh'), *args),
                                         input=line + '\n', text=True, capture_output=True,
                                         env=self.child_env(home), timeout=90)
                output = (process.stdout + '\n' + process.stderr).strip()
                receipt.update(state='saved' if process.returncode == 0 else 'uncertain',
                               saved=True if process.returncode == 0 else None,
                               message=('Review request saved.' if value == 'reconcile' else
                                        'Answer saved by Firstmate.') if process.returncode == 0 else
                                       'The intake did not confirm submission. Check the receipt; your draft is preserved.',
                               detail=fm.delivery_text(output)[-4000:])
                if process.returncode == 3:
                    try:
                        conflict = json.loads(process.stderr.strip())
                    except ValueError:
                        conflict = {}
                    if conflict.get('error') == 'identity-conflict':
                        receipt.update(state='conflict', saved=False, notice='none',
                                       message='The occurrence changed before saving. The answer was not sent; your draft is preserved.')
                # Success is durable intake acceptance, never native execution,
                # wake acknowledgement or consumption by the owning agent.
                self.collector.refresh_now()
            except (OSError, subprocess.SubprocessError, Refusal) as exc:
                receipt.update(message='Could not confirm submission. Your draft is preserved.', detail=str(exc))
            self.store_receipt(path, receipt)
            return self.present_receipt(receipt)

    def present_receipt(self, receipt):
        # Old receipt messages are bridge-owned text. Only this field is migrated
        # for display; receipt files, authored titles and intake output stay intact.
        return dict(receipt, message=self.i18n.receipt_message(receipt.get("message", "")))

    def store_receipt(self, path, receipt, exclusive=False):
        flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
        flags |= os.O_EXCL if exclusive else os.O_TRUNC
        fd = os.open(path, flags, 0o600)
        try:
            data = json.dumps(receipt, ensure_ascii=False).encode()
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)


class Security:
    def __init__(self, port, origin='', users=()):
        self.local = f'http://127.0.0.1:{port}'
        self.origin = origin.rstrip('/')
        self.users = set(users)
        self.sessions = {}
        self.lock = threading.Lock()
        if self.origin:
            parsed = urlsplit(self.origin)
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.path or
                    parsed.query or parsed.fragment or parsed.username):
                raise ValueError('CD_WEB_ORIGIN must be an exact HTTPS origin without path')
            if not self.users:
                raise ValueError('CD_WEB_USERS must explicitly name the existing tailnet user(s)')

    def check(self, handler, unsafe=False):
        host = handler.headers.get('Host', '')
        origin = handler.headers.get('Origin', '')
        forwarded = handler.headers.get('Tailscale-User-Login', '')
        if host == urlsplit(self.local).netloc and not forwarded:
            expected, actor = self.local, 'local'
        elif self.origin and host == urlsplit(self.origin).netloc and forwarded in self.users:
            expected, actor = self.origin, forwarded
        else:
            raise Refusal('Unauthorized identity or origin.', 403)
        if origin and origin != expected:
            raise Refusal('Unauthorized origin.', 403)
        # A clicked dashboard/notification link may cross sites or schemes,
        # including through a redirect. Permit only the user-activated GET
        # document entry; API reads, subresources, frames and all POSTs keep
        # the cross-site refusal. Host, identity and Origin were checked above.
        document_entry = (
            not unsafe and handler.command == 'GET' and
            urlsplit(handler.path).path in ('/', '/index.html') and
            handler.headers.get('Sec-Fetch-Mode', '') == 'navigate' and
            handler.headers.get('Sec-Fetch-Dest', '') == 'document' and
            handler.headers.get('Sec-Fetch-User', '') == '?1'
        )
        if handler.headers.get('Sec-Fetch-Site', '') == 'cross-site' and not document_entry:
            raise Refusal('Cross-site request rejected.', 403)
        if unsafe and (origin != expected or handler.headers.get('Content-Type', '').split(';')[0] != 'application/json'):
            raise Refusal('The action requires the exact origin and JSON.', 403)
        cookie = http.cookies.SimpleCookie()
        try:
            cookie.load(handler.headers.get('Cookie', ''))
        except http.cookies.CookieError:
            raise Refusal('Invalid session.', 403)
        sid = cookie['deck_session'].value if 'deck_session' in cookie else ''
        with self.lock:
            now = time.time()
            self.sessions = {k: v for k, v in self.sessions.items() if now - v[2] < 43200}
            session = self.sessions.get(sid)
            if not session or session[0] != actor:
                if unsafe:
                    raise Refusal('The session expired. Refresh without losing your draft.', 403)
                sid = secrets.token_urlsafe(32)
                session = (actor, secrets.token_urlsafe(32), now)
                self.sessions[sid] = session
            if unsafe and not secrets.compare_digest(handler.headers.get('X-Deck-CSRF', ''), session[1]):
                raise Refusal('Invalid action token. Refresh without losing your draft.', 403)
        return actor, session[1], sid, expected.startswith('https:')


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def server_bind(self):
        # HTTPServer resolves its bound address through reverse DNS. This
        # loopback-only service uses an explicit authority in Security and
        # must be ready even when the system hostname resolver is unavailable.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def __init__(self, address, board, security):
        super().__init__(address, Handler)
        self.board, self.security = board, security
        self.i18n = board.i18n if board is not None else Localization('en')


class Handler(BaseHTTPRequestHandler):
    server_version = 'CaptainsDeck/1'
    protocol_version = 'HTTP/1.0'

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def do_GET(self):
        self.handle_request(False)

    def do_POST(self):
        self.handle_request(True)

    def handle_request(self, unsafe):
        try:
            actor, csrf, sid, secure = self.server.security.check(self, unsafe)
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            if '\\' in path or '\x00' in path or any(p in ('.', '..') for p in path.split('/')):
                raise Refusal('Invalid route.', 400)
            query = parse_qs(parsed.query)
            value = lambda key: query.get(key, [''])[0]
            parts = path.strip('/').split('/')
            board = self.server.board
            body = {}
            status = 200
            if unsafe:
                if self.headers.get('Transfer-Encoding'):
                    raise Refusal('Transfer-Encoding is not allowed.', 400)
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BODY:
                    raise Refusal('Request is too large or empty.', 413)
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise Refusal('A JSON object is required.', 400)
            if not unsafe and path == '/health':
                data = board.health()
                status = 200 if data['ok'] else 503
            elif not unsafe and path == '/api/locale':
                data = self.server.i18n.client()
            elif not unsafe and path == '/api/board':
                data = board.board(value('home'))
                data['csrf'] = csrf
            elif not unsafe and path == '/api/history':
                data = board.history(value('home'), value('q')[:300])
            elif not unsafe and path == '/api/receipts':
                data = {'receipts': board.receipts(actor)}
            elif not unsafe and path == '/api/resolve':
                data = board.resolve(value('home'), value('task'), value('view'), actor, value('item')[:160])
            elif len(parts) == 3 and parts[:2] == ['api', 'ticket'] and not unsafe:
                data = board.ticket(parts[2])
            elif len(parts) == 4 and parts[:2] == ['api', 'ticket'] and parts[3] == 'answer' and unsafe:
                data = board.submit(parts[2], body, actor)
            elif len(parts) in (4, 5) and parts[:2] == ['api', 'delivery']:
                if len(parts) == 4 and not unsafe:
                    data = board.delivery(parts[2], parts[3])
                elif len(parts) == 5 and parts[4] == 'explanation':
                    data = board.explanation(parts[2], parts[3], status=not unsafe)
                else:
                    raise Refusal('Route unavailable.', 404)
            elif not unsafe and not path.startswith('/api/'):
                rel = path.lstrip('/') or 'index.html'
                try:
                    content = fm.delivery_read(str(STATIC), rel, 2 * 1024 * 1024).encode() if not rel.endswith('.png') else self.read_binary(rel)
                except (OSError, ValueError):
                    raise Refusal('File unavailable.', 404)
                self.respond(200, content, mimetypes.guess_type(rel)[0] or 'application/octet-stream', sid, secure)
                return
            else:
                raise Refusal('Route unavailable.', 404)
            self.respond(status, json.dumps(data, ensure_ascii=False).encode(), 'application/json; charset=utf-8', sid, secure)
        except (Refusal, ValueError, json.JSONDecodeError) as exc:
            self.respond(getattr(exc, 'status', 400), json.dumps({'error': self.server.i18n.t(str(exc))}).encode())
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            print(f'bridge request failed: {type(exc).__name__}: {exc}', file=sys.stderr)
            self.respond(500, json.dumps({'error': self.server.i18n.t('Could not complete the request. Your draft is preserved.')}).encode())

    def read_binary(self, relative):
        # Icons are repo-owned static files, never a report path from a client.
        if not re.fullmatch(r'icons/[a-zA-Z0-9-]+\.png', relative):
            raise ValueError('unsafe asset')
        path = STATIC / relative
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError('symlink asset')
        return path.read_bytes()

    def respond(self, status, content, mime='application/json; charset=utf-8', sid='', secure=False):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if sid:
            self.send_header('Set-Cookie', f'deck_session={sid}; HttpOnly; SameSite=Strict; Path=/' + ('; Secure' if secure else ''))
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):
        # Paths, query strings and captain text are intentionally not logged.
        if args and str(args[1] if len(args) > 1 else '') not in ('200', '304'):
            print('deck HTTP request completed', file=sys.stderr)


def main(argv=None, *, stop_event=None, on_ready=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', default=os.environ.get('CD_WEB_BIND', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('CD_WEB_PORT', '8787')))
    args = parser.parse_args(argv)
    if args.bind != '127.0.0.1':
        parser.error('only 127.0.0.1 is supported; use private Tailscale Serve for HTTPS')
    if not 1024 <= args.port <= 65535:
        parser.error('port must be 1024..65535')
    data = os.environ.get('CD_WEB_DATA', str(private_directory('data')))
    herdr = os.environ.get('CD_WEB_HERDR_ORIGIN', '').rstrip('/')
    if herdr and (urlsplit(herdr).scheme != 'https' or not urlsplit(herdr).hostname or
                  urlsplit(herdr).path or urlsplit(herdr).username or
                  urlsplit(herdr).query or urlsplit(herdr).fragment):
        parser.error('CD_WEB_HERDR_ORIGIN must be an exact HTTPS origin')
    security = Security(args.port, os.environ.get('CD_WEB_ORIGIN', ''),
                        filter(None, os.environ.get('CD_WEB_USERS', '').split(',')))
    board = Board(data, herdr)
    server = Server((args.bind, args.port), board, security)
    board.start()
    def stop(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if stop_event is not None:
        def watch_stop():
            stop_event.wait()
            server.shutdown()
        threading.Thread(target=watch_stop, daemon=True).start()
    if on_ready:
        on_ready()
    print(f'Captain’s Deck listening at http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        board.stop()
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
