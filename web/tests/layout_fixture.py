#!/usr/bin/env python3
"""Synthetic board for the real-browser layout check; never production.

Usage: layout_fixture.py /absolute/scratch-dir [es|en]. Serves a free loopback
port and prints its URL. Titles and lines are long on purpose.
"""
from pathlib import Path
import os
import socket
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
root = Path(sys.argv[1]).resolve()
language = sys.argv[2] if len(sys.argv) > 2 else 'es'
os.environ['HERDR_PLUGIN_STATE_DIR'] = str(root / 'plugin-state')
os.environ['HERDR_PLUGIN_CONFIG_DIR'] = str(root / 'plugin-config')
from fixtures import FakeCollector, bridge, fixture  # noqa: E402
import activity_fleet  # noqa: E402

_, home = fixture(root)
f = bridge.fm
now = time.time()
data = activity_fleet.payload(now, True)
(Path(home.path) / 'data/relay-0b').mkdir(parents=True, exist_ok=True)
(Path(home.path) / 'data/relay-0b/report.md').write_text('# Informe\n\n## Resultado\nLa medición quedó lista.\n')


def call(id, title, question, context, options, recommended='', link='', project=None):
    return dict(id=id, key=id, verb='captain-hold', owner='(main)', title=title, summary=title,
                flow={'phase': 'human', 'state': 'held', 'source': 'backlog.hold_reason', 'reason': context,
                      **({'project': project} if project else {})},
                call={'question': question, 'context': context, 'options': options, 'recommended': recommended, 'link': link})


data['decisions_open'] = [
    call('relay-phase-1', '¿Arranco la fase 1 del relevo?', '¿Arranco la fase 1 del relevo?',
         'La fase 0B ya terminó. Se crea el repo nuevo y un worker que mide.',
         [{'id': 'start', 'label': 'Sí, arráncala', 'detail': 'Solo mide y te avisa.'}, {'id': 'wait', 'label': 'Todavía no', 'detail': ''}],
         'start', 'data/relay-0b/report.md', project={'id': 'relay', 'label': 'Relevo'}),
    call('design-calls', 'Decisiones de diseño para la fase 2 del relevo asistido por contexto, con cuatro preguntas abiertas sobre límites',
         '¿Las vemos ahora o cuando termine la fase 1?', 'No frenan la fase 1.',
         [{'id': 'now', 'label': 'Verlas ahora', 'detail': ''}, {'id': 'later', 'label': 'Después', 'detail': ''}]),
] + [call(f'da-ux-q-{i}', title, title, 'De tu sesión de diseño.',
          [{'id': 'yes', 'label': 'Sí', 'detail': ''}, {'id': 'no', 'label': 'No', 'detail': ''}], 'yes',
          project='Day Attendant')
     for i, title in enumerate(['¿Animación al mover tareas?', '¿Un grupo o varios por tarea, y qué pasa con las tareas que ya tienen uno?',
                                '¿Cuántas líneas por tarea?'])] + data['decisions_open']
# Projects as Firstmate records them; the rest of the work has none.
for row, project in zip(data['in_flight'], ['Day Attendant', {'id': 'relay', 'label': 'Relevo'}, 'Firstmate']):
    row['flow']['project'] = project
for row in data['in_flight'][:2]:
    row['name'] = row['name'] + ' and keep every existing integration, retry budget and audit trail intact while it migrates'
data['landed'] = [{'id': 'phone', 'what': 'Atención del teléfono preparada; validación física pendiente en el dispositivo real del capitán', 'flow': {'project': 'Day Attendant'},
                   'owner': '(main)', 'closed': time.strftime('%Y-%m-%d', time.gmtime(now))}]
# A long history like the captain's: timed, date-only and untimed rows out
# of order, some titles still carrying a PR address.
topics = ['Avisos con nombre propio', 'Las pruebas ya no tocan tus servicios reales',
          'El supervisor ya delega en vez de hacer encargos', 'Investigación de buenas prácticas de UI lista',
          'Las tareas sin trabajo ya no se quedan en «En curso» y se cierran solas cuando el agente termina sin avisar',
          'Tablero del Deck con opciones claras y un texto corto https://github.com/example/firstmate/pull/8']
records = []
for i in range(87):
    stamp = now - (i * 37 % 87) * 23000
    completion = ({'value': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(stamp))} if i % 4 else
                  {'date': time.strftime('%Y-%m-%d', time.gmtime(stamp))} if i % 8 else None)
    records.append({'id': f'delivery-{i:02d}', 'title': topics[i % len(topics)] + f' ({i})' * (i % len(topics) != 5),
                    'state': 'done', 'kind': 'ship', 'source': 'archive', **({'completion': completion} if completion else {}),
                    **({'project': ['Firstmate', 'Deck', 'Day Attendant'][i % 3]} if i % 2 else {})})
(Path(home.path) / 'bin/history.py').write_text(
    'import json,os\nprint(json.dumps({"schema":"fm-delivery-history.v1","home":os.environ["FM_HOME"],"complete":True,'
    '"retention":{},"sources":["archive"],"records":' + repr(records).replace("'", '"').replace('None', 'null') + '}))\n')
snap = f.Snapshot(); snap.homes = f.inject_all_crew([home]); snap.home = snap.homes[0]; snap.collected_at = now
snap.cols, snap.totals = f.make_cards(home, data, {}, {}, {}, snap.homes)
board = bridge.Board(root / 'web-data', '', FakeCollector(snap, {home.label: (data, now, home.path)}), language=language)
with socket.socket() as probe:
    probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
server = bridge.Server(('127.0.0.1', port), board, bridge.Security(port))
print(f'URL http://127.0.0.1:{port}/', flush=True)
server.serve_forever()
