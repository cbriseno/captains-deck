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
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'scripts'))
import flow_tui as fm  # noqa: E402
from deck_platform import endpoint_path, private_directory, safe_file_reads, shell_command

STATIC = HERE / 'static'
COLUMNS = [('charted', 'Por comenzar'), ('underway', 'En curso'),
           ('captains_call', 'Tu decisión'), ('landed', 'Entregado')]
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


def event_json(event):
    if not isinstance(event, fm.CardTime):
        event = fm.CardTime('Updated')
    return {'label': event.label, 'epoch': event.epoch,
            'date': event.day.isoformat() if event.day else None,
            'source': event.source, 'problem': event.problem,
            'detail': fm.card_time_detail(event)}


def human_title(value):
    # Canonical titles sometimes retain a report link as a trailing token.
    # Keep the original in Details; a filesystem location is not the headline.
    text = fm.readable_markdown(str(value or ''))
    text = re.sub(r'(?<!\S)(?:data/|/[^\s]*/data/)[^\s]+\.(?:md|json|txt)(?=\s|$)', '', text)
    return re.sub(r'\s+', ' ', text).strip(' ·–-') or 'Tarea sin título legible'


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
            self.source_errors[home.label] = '' if after > before else 'No se pudo actualizar Firstmate.'
        return result


class Board:
    def __init__(self, data_dir, herdr_origin='', collector=None):
        self.collector = collector or Collector()
        self.data_dir = Path(data_dir).expanduser().absolute()
        self.herdr_origin = herdr_origin
        self.mutation_lock = threading.Lock()
        self.history_lock = threading.Lock()
        self.history_cache = {}

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
            raise Refusal('El home ya no está disponible o su identidad es ambigua.', 404)
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
            raise Refusal('El responsable de esta tarea no está confirmado.')
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
            raise Refusal('Esta tarea cambió o ya no está en la vista. Actualiza el tablero.', 404)
        return matches[0]

    def card_json(self, card):
        owner = None
        try:
            owner = self.owner(card)
        except Refusal:
            pass
        context = fm.flow_context(card)
        report_state, report_text, report_time = fm.durable_report(card)
        pane = str(getattr(card, 'pane_id', '') or '')
        # The installed Herdr Web supports ?machine=local&pane=…&view=chat.
        # Remote machine mapping is deliberately not guessed from a route label.
        link = ''
        if self.herdr_origin and owner and not fm.is_remote_home(owner) and pane:
            link = self.herdr_origin + '/?' + urlencode({'machine': 'local', 'pane': pane, 'view': 'chat'})
        return {'key': self.key(card), 'task': card.task, 'title': human_title(card.title),
                'original_title': card.title,
                'bucket': card.bucket, 'owner': owner.label if owner else 'Responsable sin confirmar',
                'home': home_id(owner) if owner else '', 'context': context,
                'report': {'state': report_state, 'text': report_text, 'time': event_json(report_time)},
                'native': {'state': getattr(card, 'snapshot_state', '') or 'unknown',
                           'detail': getattr(card, 'doing', '') or 'Sin observación registrada.',
                           'herdr': getattr(card, 'live_status', '') or 'unknown'},
                'dependencies': getattr(card, 'dependencies', []) or [],
                'supervisor': getattr(card, 'supervisor', {}) or {},
                'time': event_json(getattr(card, 'event_time', None)),
                'summary': getattr(card, 'delivery_summary', '') or '',
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
            columns.append({'id': key, 'title': title, 'cards': cards})
        sources = []
        for h in homes:
            if chosen and home_id(h) != selected:
                continue
            raw, received, _ = self.collector._raw.get(h.label, ({}, 0, ''))
            error = getattr(self.collector, 'source_errors', {}).get(h.label, '')
            sources.append({'home': home_id(h), 'label': h.label,
                            'received_at': received, 'generated': raw.get('generated', ''),
                            'error': error, 'remote': fm.is_remote_home(h),
                            'supervisor': raw.get('supervisor') or {}})
        return {'seq': snap.seq, 'collected_at': snap.collected_at,
                'server_at': time.time(), 'loading': bool(snap.loading),
                'error': snap.error, 'sources': sources,
                'homes': [{'id': home_id(h), 'label': h.label} for h in homes],
                'columns': columns, 'home': selected,
                'source_ttl': fm._bearings_ttl(),
                'retention': 'Recientes es una vista limitada. El historial busca sólo entregas retenidas; no recupera registros borrados.'}

    def receipts(self, actor):
        directory = self.data_dir / 'answers'
        if not directory.exists():
            return []
        if directory.is_symlink():
            raise Refusal('Directorio de recibos inseguro.')
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
        return sorted(result, key=lambda r: r.get('at', 0), reverse=True)[:10]

    def resolve(self, identity, task, view, actor, item=''):
        if not fm.KEY_RE.fullmatch(task) or view not in ('call', 'delivery'):
            raise Refusal('Enlace de tarea inválido.', 400)
        home = self.home(identity)
        if view == 'delivery':
            try:
                return {'view': 'delivery', 'delivery': self.delivery(identity, task),
                        'notice': 'La entrega se identifica por home y tarea. El historial no permite verificar el identificador del aviso original.' if item else ''}
            except Refusal as exc:
                return {'view': 'unavailable', 'task': task, 'owner': home.label, 'message': str(exc), 'receipts': []}
        matches = []
        for card in self.collector.snapshot().cols.get('captains_call', []):
            try:
                if card.task == task and home_id(self.owner(card)) == identity:
                    matches.append(card)
            except Refusal:
                continue
        message = 'Esta tarea ya no aparece entre las preguntas actuales. Puede haberse resuelto, reabierto o cambiado de estado; este enlace no prueba cuál. Consulta el tablero o sus recibos.'
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
                message = ('El aviso corresponde a una pregunta anterior o a una identidad no verificable. '
                           'No se presenta la pregunta actual como si fuera la del aviso. Puedes abrirla explícitamente para revisarla.')
            except Refusal:
                pass
        if occurrence and not fm.is_remote_home(home):
            try:
                result = subprocess.run(shell_command(endpoint_path(home.path, 'fm-captain-hold.sh'),
                                         'resolved', task, occurrence[1]), env=self.child_env(home),
                                        capture_output=True, text=True, timeout=15,
                                        stdin=subprocess.DEVNULL)
                if result.returncode == 0:
                    message = 'La pregunta de este aviso ya está resuelta, según el intake canónico de Firstmate. Este enlace no vuelve a enviar una respuesta.'
            except (OSError, subprocess.SubprocessError):
                pass
        return {'view': 'unavailable', 'task': task, 'owner': home.label,
                'message': message, 'current_key': current_key,
                'receipts': [r for r in self.receipts(actor) if r.get('home') == identity and r.get('task') == task]}

    def records(self, home, force=False):
        identity = home_id(home)
        with self.history_lock:
            rows, note, at = self.history_cache.get(identity, ([], '', 0))
            if force or time.time() - at > 15:
                rows, note = fm.delivery_records(home)
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
                result.append({'task': row['id'], 'title': human_title(row.get('title') or row['id']),
                               'home': home_id(home), 'owner': home.label,
                               'time': event_json(event), 'closed': row.get('closed') or ''})
        result.sort(key=lambda row: (row['closed'], row['task'], row['home']), reverse=True)
        return {'records': result[:1000], 'omitted': max(0, len(result) - 1000),
                'details': details, 'scope': 'Entregas retenidas por Firstmate. Recientes tiene un límite distinto; buscar no cambia la retención.'}

    def delivery(self, identity, task):
        home = self.home(identity)
        if not fm.KEY_RE.fullmatch(task):
            raise Refusal('Identidad de tarea inválida.', 400)
        rows, note = self.records(home, force=True)
        matches = [r for r in rows if r['id'] == task]
        if len(matches) != 1:
            raise Refusal('Esta entrega ya no está retenida o la tarea se reabrió.', 404)
        record = matches[0]
        content = fm.delivery_content(record, home, split_views=True)
        if not isinstance(content, dict):
            content = {'summary': 'No hay resumen disponible para esta entrega.', 'report': '', 'details': content}
        # The TUI's report lead is a keyboard hint/path; the web has its own tabs.
        content['report'] = re.sub(r'^Informe completo[^\n]*\n*', '', content['report'])
        return {'task': task, 'home': identity, 'owner': home.label,
                'title': human_title(record.get('title') or task), **content, 'sources': note,
                'time': event_json(fm.producer_card_time(record.get('card_time'), 'landed',
                                                       fm.CardTime('Delivered', record.get('closed'))))}

    def explanation(self, identity, task, status=True):
        home = self.home(identity)
        if not fm.KEY_RE.fullmatch(task):
            raise Refusal('Identidad de tarea inválida.', 400)
        rows, _ = self.records(home, force=not status)
        if len([row for row in rows if row['id'] == task]) != 1:
            raise Refusal('Esta entrega ya no está retenida o la tarea se reabrió.', 404)
        return fm.explanation_request(home, task, status=status)

    def call_identity(self, home, task):
        if fm.is_remote_home(home) or not fm.KEY_RE.fullmatch(task):
            raise Refusal('Este home no ofrece respuestas web verificables.')
        script = Path(endpoint_path(home.path, 'fm-captain-hold.sh'))
        try:
            result = subprocess.run(shell_command(script, 'open', task, '--identity'),
                                    env=self.child_env(home), capture_output=True, text=True,
                                    stdin=subprocess.DEVNULL, timeout=15)
        except (OSError, subprocess.SubprocessError):
            raise Refusal('No se pudo comprobar la pregunta actual. Se conserva el borrador.')
        identity = result.stdout.strip()
        if result.returncode != 0 or not re.fullmatch(r'[^\s#]+#\d+', identity):
            raise Refusal('La pregunta ya no está abierta o su ocurrencia no se puede verificar.')
        return identity

    @staticmethod
    def child_env(home):
        env = fm._child_env()
        # External intake, never impersonate Root or inherit a worker namespace.
        for key in list(env):
            if key.startswith('FM_') or key in ('CODEX_THREAD_ID', 'CODEX_SESSION_ID', 'CLAUDE_SESSION_ID',
                                               'HERDR_PANE_ID', 'HERDR_TAB_ID', 'HERDR_WORKSPACE_ID'):
                env.pop(key, None)
        env['FM_HOME'] = home.path
        return env

    def content(self, card, home):
        # The confirmed owning home is the active context, never aggregate All.
        content = fm.decision_card_content(card, self.homes(), home)
        if os.path.realpath(content['home_path']) != fm.delivery_home_identity(home):
            raise Refusal('El intake no coincide con el home responsable.')
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
            error = '' if guarded else 'El intake de este home necesita el guard de ocurrencia para responder desde web.'
        except Refusal as exc:
            identity, error = '', str(exc)
        result['call'] = {k: content[k] for k in ('title', 'about', 'decide', 'detail', 'options',
                                                   'recommend', 'freeform', 'freeform_hint', 'close')}
        result['call']['identity'] = identity
        result['call']['occurrence'] = digest(home_id(home), card.task, identity, content['stamp'],
                                            fm.flow_context(card)['reason'], result['call'])
        result['call']['error'] = error
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
            raise Refusal('Formato de respuesta inválido.', 400)
        request_id = body.get('request_id', '')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{16,80}', request_id):
            raise Refusal('Falta el identificador de envío.', 400)
        occurrence = body.get('occurrence', '')
        if not re.fullmatch(r'[a-f0-9]{64}', occurrence):
            raise Refusal('Falta la ocurrencia de la pregunta.', 400)
        # One immutable receipt per occurrence, across clients and server restarts.
        # Lock is held across validation/intake; Firstmate additionally owns CAS.
        if not safe_file_reads():
            raise Refusal('El backend seguro de respuestas requiere POSIX; ejecuta Deck y Firstmate en WSL.', 503)
        with self.mutation_lock:
            receipt_dir = self.data_dir / 'answers'
            if receipt_dir.is_symlink():
                raise Refusal('Directorio de recibos inseguro.')
            path = receipt_dir / (digest(key, occurrence) + '.json')
            payload_hash = digest(key, occurrence, body.get('value', ''), body.get('note', ''))
            if path.exists() or path.is_symlink():
                try:
                    previous = json.loads(fm.delivery_read(str(self.data_dir), str(path), 65536))
                except (OSError, ValueError):
                    raise Refusal('Recibo no verificable; Firstmate debe revisar este envío.')
                if previous.get('payload_hash') != payload_hash or previous.get('actor') != actor:
                    raise Refusal('Esta ocurrencia ya tiene otro envío. Tu borrador se conserva.')
                return dict(previous, replay=True)
            card = self.card(key)
            if card.bucket != 'captains_call':
                raise Refusal('La tarea ya no espera una respuesta humana.')
            home = self.owner(card)
            ticket = self.ticket(key)
            call = ticket['call']
            if call['error']:
                raise Refusal(call['error'])
            if not secrets.compare_digest(call['occurrence'], occurrence):
                raise Refusal('La pregunta cambió. Revisa la versión actual; tu borrador se conserva.')
            value, note = body.get('value', '').strip(), body.get('note', '').strip()
            # The installed keyed intake bounds each field to 512 bytes. Reject
            # rather than silently truncate the captain's words.
            if any(ch in value + note for ch in ('\x00', '\t', '\n', '\r')):
                raise Refusal('La respuesta debe ocupar una sola línea; no se ha enviado.', 400)
            if len(note.encode()) > min(fm.ANSWER_LIMIT, 512):
                raise Refusal('La respuesta supera 512 bytes; acórtala antes de enviarla.', 400)
            options = call['options']
            if value and value not in [o['value'] for o in options]:
                raise Refusal('Esa opción ya no está disponible.')
            if (not value and (not call['freeform'] or not note)):
                raise Refusal('Elige una opción o escribe una respuesta.', 400)
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
                raise Refusal('La opción y el texto juntos superan el límite del intake.', 400)
            # No write until all checks pass. The pending marker is deliberately
            # durable: after an interrupted call retry cannot submit twice.
            receipt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            receipt = {'request_id': request_id, 'payload_hash': payload_hash, 'actor': actor,
                       'home': home_id(home), 'task': card.task, 'title': card.title, 'occurrence': occurrence,
                       'at': time.time(), 'saved': None, 'notice': 'unknown',
                       'ack': 'unknown', 'consumed': 'unknown', 'state': 'uncertain',
                       'message': 'Envío iniciado. Si la conexión se interrumpe, verifica este recibo antes de repetir.'}
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
                            raise Refusal('No se pudo vincular el intake de revisión.')
                args += ['--expected-identity', call['identity']]
                process = subprocess.run(shell_command(endpoint_path(home.path, 'fm-captain-hold.sh'), *args),
                                         input=line + '\n', text=True, capture_output=True,
                                         env=self.child_env(home), timeout=90)
                output = (process.stdout + '\n' + process.stderr).strip()
                receipt.update(state='saved' if process.returncode == 0 else 'uncertain',
                               saved=True if process.returncode == 0 else None,
                               message=('Solicitud de revisión guardada.' if value == 'reconcile' else
                                        'Respuesta guardada por Firstmate.') if process.returncode == 0 else
                                       'El intake no confirmó el envío. Verifica el recibo; tu borrador se conserva.',
                               detail=fm.delivery_text(output)[-4000:])
                if process.returncode == 3:
                    try:
                        conflict = json.loads(process.stderr.strip())
                    except ValueError:
                        conflict = {}
                    if conflict.get('error') == 'identity-conflict':
                        receipt.update(state='conflict', saved=False, notice='none',
                                       message='La ocurrencia cambió antes de guardar. No se envió la respuesta; tu borrador se conserva.')
                # Success is durable intake acceptance, never native execution,
                # wake acknowledgement or consumption by the owning agent.
                self.collector.refresh_now()
            except (OSError, subprocess.SubprocessError, Refusal) as exc:
                receipt.update(message='No se pudo confirmar el envío. Tu borrador se conserva.', detail=str(exc))
            self.store_receipt(path, receipt)
            return receipt

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
            raise Refusal('Identidad u origen no autorizado.', 403)
        if origin and origin != expected:
            raise Refusal('Origen no autorizado.', 403)
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
            raise Refusal('Petición de otro sitio rechazada.', 403)
        if unsafe and (origin != expected or handler.headers.get('Content-Type', '').split(';')[0] != 'application/json'):
            raise Refusal('La acción requiere el origen exacto y JSON.', 403)
        cookie = http.cookies.SimpleCookie()
        try:
            cookie.load(handler.headers.get('Cookie', ''))
        except http.cookies.CookieError:
            raise Refusal('Sesión inválida.', 403)
        sid = cookie['deck_session'].value if 'deck_session' in cookie else ''
        with self.lock:
            now = time.time()
            self.sessions = {k: v for k, v in self.sessions.items() if now - v[2] < 43200}
            session = self.sessions.get(sid)
            if not session or session[0] != actor:
                if unsafe:
                    raise Refusal('La sesión expiró. Actualiza sin perder el borrador.', 403)
                sid = secrets.token_urlsafe(32)
                session = (actor, secrets.token_urlsafe(32), now)
                self.sessions[sid] = session
            if unsafe and not secrets.compare_digest(handler.headers.get('X-Deck-CSRF', ''), session[1]):
                raise Refusal('Token de acción inválido. Actualiza sin perder el borrador.', 403)
        return actor, session[1], sid, expected.startswith('https:')


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, address, board, security):
        super().__init__(address, Handler)
        self.board, self.security = board, security


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
                raise Refusal('Ruta inválida.', 400)
            query = parse_qs(parsed.query)
            value = lambda key: query.get(key, [''])[0]
            parts = path.strip('/').split('/')
            board = self.server.board
            body = {}
            status = 200
            if unsafe:
                if self.headers.get('Transfer-Encoding'):
                    raise Refusal('Transfer-Encoding no permitido.', 400)
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BODY:
                    raise Refusal('Petición demasiado grande o vacía.', 413)
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise Refusal('Se necesita un objeto JSON.', 400)
            if not unsafe and path == '/health':
                data = board.health()
                status = 200 if data['ok'] else 503
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
                    raise Refusal('Ruta no disponible.', 404)
            elif not unsafe and not path.startswith('/api/'):
                rel = path.lstrip('/') or 'index.html'
                try:
                    content = fm.delivery_read(str(STATIC), rel, 2 * 1024 * 1024).encode() if not rel.endswith('.png') else self.read_binary(rel)
                except (OSError, ValueError):
                    raise Refusal('Archivo no disponible.', 404)
                self.respond(200, content, mimetypes.guess_type(rel)[0] or 'application/octet-stream', sid, secure)
                return
            else:
                raise Refusal('Ruta no disponible.', 404)
            self.respond(status, json.dumps(data, ensure_ascii=False).encode(), 'application/json; charset=utf-8', sid, secure)
        except (Refusal, ValueError, json.JSONDecodeError) as exc:
            self.respond(getattr(exc, 'status', 400), json.dumps({'error': str(exc)}).encode())
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            print(f'bridge request failed: {type(exc).__name__}: {exc}', file=sys.stderr)
            self.respond(500, b'{"error":"No se pudo completar la consulta. El borrador se conserva."}')

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
