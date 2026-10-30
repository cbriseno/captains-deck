"""Synthetic fleet where most open work is not being worked on right now.

Mirrors a reported board: many cards under Underway and Up next, while only
three agents were working. The rest wait on their own validation, another
task, the person or a date, or have no live agent. Rows use the fields
fm-bearings.v1 exports with ``--fields paths,endpoints``. ``firstmate_fields``
adds the hold_bucket/hold_until, wait_until and state_source fields that a
companion Firstmate change adds; they stay absent until it lands, so both the
present and the degraded paths stay testable.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import FakeCollector, bridge


def _registration(harness=None, run_step=None):
    execution = {'harness': harness, 'role': 'ship', 'assignment_state': 'registered', 'source': 'task.meta'} if harness else None
    return {'execution': execution,
            'run_step': run_step,
            'native_activity': {'state': 'unknown', 'verified': False, 'observed_at': None, 'source': None, 'identity': None}}


def _flow(phase, state, source, reason, declared=None, summary=None, waiting=None, deps=()):
    return {'phase': phase, 'state': state, 'source': source, 'reason': reason, 'declared_state': declared,
            'presentation': {'summary': summary, 'waiting_for': waiting, 'next_action': None},
            'waiting_for': waiting, 'next_action': None,
            'dependencies': [{'id': d, 'title': title, 'owner': '(main)', 'state': dep_state, 'unresolved': True}
                             for d, title, dep_state in deps]}


def payload(now=None, firstmate_fields=False):
    now = time.time() if now is None else now
    stamp = datetime.fromtimestamp(now, timezone.utc)
    day = lambda n: (stamp + timedelta(days=n)).date().isoformat()
    observed = stamp.strftime('%Y-%m-%dT%H:%M:%SZ')
    running = {'phase': 'validation', 'state': 'running', 'declared_state': 'running', 'run_id': 'SYNTHETIC-RUN-1',
               'observed_at': observed, 'source': 'no-mistakes.attributed-run', 'ttl_seconds': 60,
               'freshness': 'fresh', 'age_seconds': 0}
    # A run parked at a gate still reports the run itself as running.
    parked = dict(running, run_id='SYNTHETIC-RUN-2', state='unknown', freshness='stale')
    working = lambda id, name, harness, summary: dict(
        _registration(harness), id=id, name=name, kind='ship', state='working', blocked_by='-',
        doing='harness busy (claude-hook)',
        flow=_flow('active', 'executing', 'current_state.pane', 'harness busy (claude-hook)', 'working', summary))
    in_flight = [
        working('api-pagination', 'Paginate the public API listing', 'claude',
                'Cursor pagination works; edge-case tests are being added.'),
        working('search-index', 'Rebuild the search index incrementally', 'pi',
                'The incremental builder is in place; timing checks are running locally.'),
        working('docs-refresh', 'Refresh the setup guide', 'claude',
                'The install section is rewritten; screenshots are next.'),
        dict(_registration('claude', running), id='release-notes', name='Write the release notes', kind='ship',
             state='working', blocked_by='-', doing='validating (running) · run: SYNTHETIC-RUN-1',
             flow=_flow('active', 'executing', 'current_state.run-step', 'validating (running) · run: SYNTHETIC-RUN-1',
                        'paused', 'The notes are written; the worker paused while its validation runs.')),
        dict(_registration('codex', parked), id='billing-export', name='Export invoices for billing', kind='ship',
             state='parked', blocked_by='-', doing='parked at review: 2 finding(s) · run: SYNTHETIC-RUN-2',
             flow=_flow('technical', 'waiting', 'current_state.run-step', 'parked at review: 2 finding(s)', 'paused',
                        'The export is built; validation stopped at its review gate.', 'A review answer for the validation run.')),
        dict(_registration('claude'), id='cache-migration', name='Migrate the cache layer', kind='ship',
             state='paused', blocked_by='-', doing='Stopped at the person’s request until the chosen date.',
             flow=_flow('technical', 'waiting', 'current_state.status-log', 'Stopped at the person’s request until the chosen date.',
                        'paused', 'Stopped on purpose; nothing runs until the chosen date.')),
        dict(_registration('codex'), id='log-rotation', name='Rotate the service logs', kind='ship',
             state='unknown', blocked_by='-', doing='harness state unavailable (endpoint missing)',
             flow=_flow('unknown', 'unknown', 'status.declaration (historical)', 'Stopped until the chosen date; the agent was closed.',
                        'paused', 'Stopped on purpose; the agent was closed until the chosen date.')),
        dict(_registration('codex'), id='notes-sync', name='Sync the meeting notes', kind='ship',
             state='unknown', blocked_by='-', doing='harness state unavailable (unknown codex-unverified)',
             flow=_flow('unknown', 'unknown', 'status.declaration (historical)', 'Finished; kept open for a personal session.',
                        'done', 'Finished. The workspace stays open only to protect a personal session.',
                        'Kept open while the personal session it protects is in use.')),
        dict(_registration('claude'), id='mobile-publish', name='Publish the mobile build', kind='ship',
             state='paused', blocked_by='billing-export', doing='Waiting for the billing export to land.',
             flow=_flow('dependency', 'waiting', 'current_state.status-log', 'Waiting for the billing export to land.', 'paused',
                        'The build is ready; publishing waits for the billing export.', 'Export invoices for billing',
                        [('billing-export', 'Export invoices for billing', 'in_flight')])),
        # The last status line says working, but the pane is idle and no run is attributed.
        dict(_registration('codex'), id='queue-retry', name='Retry failed queue jobs', kind='ship',
             state='working', blocked_by='-', doing='working: retry backoff added',
             flow=_flow('active', 'executing', 'current_state.status-log', 'working: retry backoff added', 'working',
                        'Backoff is added; the agent ended its turn.')),
        # A held row replaces the crew-state source with the hold reason.
        dict(_registration('claude'), id='theme-tokens', name='Extract the theme tokens', kind='ship',
             state='working', blocked_by='-', doing='harness busy (claude-hook)',
             flow=_flow('active', 'executing', 'backlog.hold_reason', 'Held: the person reviews the palette first.', 'working',
                        'The tokens are extracted; the palette waits for the person.')),
    ]
    gate = lambda id, title, filed, flow, blocked='-', reason='-': dict(
        _registration('claude' if id == 'pilot-rollout' else None), id=id, title=title, filed=filed, owner='(main)',
        blocked_by=blocked, reason=reason, flow=flow)
    gates = [
        gate('invoice-email', 'Email invoices after publishing', day(-1),
             _flow('dependency', 'waiting', 'unknown', 'Motivo no registrado', waiting='Publish the mobile build',
                   deps=[('mobile-publish', 'Publish the mobile build', 'in_flight')]), 'mobile-publish'),
        gate('pricing-page', 'Show the new prices', day(-1),
             _flow('dependency', 'waiting', 'unknown', 'Motivo no registrado', waiting='Export invoices for billing',
                   deps=[('billing-export', 'Export invoices for billing', 'in_flight')]), 'billing-export'),
        gate('pricing-followup', 'Announce the new prices', day(-1),
             _flow('dependency', 'waiting', 'unknown', 'Motivo no registrado', waiting='Show the new prices',
                   deps=[('pricing-page', 'Show the new prices', 'queued')]), 'pricing-page'),
        # Firstmate renders dated holds into a truncated reason; the date is not a field.
        gate('device-trial', 'Run the device trial', day(-3),
             _flow('human', 'waiting', 'backlog.hold_reason', 'Run the trial together with the person.'),
             reason='until ' + day(6) + ': Run the trial together…'),
        gate('pilot-rollout', 'Roll out the pilot', day(-4),
             _flow('human', 'held', 'backlog.hold_reason', 'Pilot paused by the person.',
                   waiting='The person resumes the pilot after the review.'),
             reason='until ' + day(8) + ': Pilot paused by the pe…'),
        gate('readme-badges', 'Add status badges to the README', day(0),
             _flow('unknown', 'ready', 'unknown', 'Motivo no registrado')),
    ]
    decisions = [dict(id='scope-choice', key='scope-choice', verb='captain-hold', owner='(main)',
                      title='Choose the beta scope', summary='Choose the beta scope: two options are ready',
                      flow=_flow('human', 'held', 'backlog.hold_reason', 'Choose which features enter the beta.'))]
    live = {'api-pagination', 'search-index', 'docs-refresh', 'release-notes', 'billing-export', 'notes-sync', 'mobile-publish',
            'queue-retry', 'theme-tokens'}
    endpoints = [{'id': row['id'], 'backend': 'herdr', 'target': 'synthetic', 'exists': row['id'] in live, 'agent': 'not_checked'}
                 for row in in_flight + [gates[4]]]
    if firstmate_fields:
        sources = {'api-pagination': 'pane', 'search-index': 'pane', 'docs-refresh': 'pane', 'release-notes': 'run-step',
                   'billing-export': 'run-step', 'cache-migration': 'status-log', 'log-rotation': 'none',
                   'notes-sync': 'pane', 'mobile-publish': 'status-log', 'queue-retry': 'status-log',
                   'theme-tokens': 'pane'}
        for row in in_flight:
            row['state_source'] = sources[row['id']]
        until = stamp.replace(hour=15, minute=0, second=0) + timedelta(days=7)
        in_flight[5]['wait_until'] = in_flight[6]['wait_until'] = until.strftime('%Y-%m-%dT%H:%M:%SZ')
        gates[3].update(hold_bucket='dated', hold_until=day(6))
        gates[4].update(hold_bucket='dated', hold_until=day(8))
    return {'schema': 'fm-bearings.v1', 'generated': observed, 'supervisor': None,
            'in_flight': in_flight, 'gates': gates, 'decisions_open': decisions,
            'landed': [{'id': 'changelog', 'what': 'Publish the changelog page', 'owner': '(main)', 'closed': day(-2)}],
            'endpoints': endpoints, 'paths': []}


def fleet(root, firstmate_fields=False, language='en'):
    """A Board over the synthetic fleet; nothing here reads a real home."""
    root = Path(root)
    home = root / 'home'
    (home / 'state').mkdir(parents=True, exist_ok=True)
    (home / 'open').touch()
    f = bridge.fm
    h = f.Home('Firstmate', str(home), 'captain')
    now = time.time()
    data = payload(now, firstmate_fields)
    snap = f.Snapshot(); snap.homes = f.inject_all_crew([h]); snap.home = snap.homes[0]; snap.collected_at = now
    snap.cols, snap.totals = f.make_cards(h, data, {}, {}, {}, snap.homes)
    board = bridge.Board(root / 'web-data', '', FakeCollector(snap, {h.label: (data, now, h.path)}), language=language)
    return board, h


def unconfirmed_fleet(root, language='es-MX'):
    """Codex unknown despite an open endpoint; done links are not proof of a wait."""
    board, home = fleet(root, True, language)
    data = payload(firstmate_fields=True)
    rows = []
    for task, title, deps in [
        ('unknown', 'Preparar la medición del relevo con todos sus detalles y conservar las decisiones aprobadas', []),
        ('resolved', 'Revisar los conectores entre tareas',
         [dict(id='previous', title='Entrega anterior', state='done', unresolved=False)]),
        ('reconcile', 'Comprobar los grupos visibles',
         [dict(id='previous', title='Entrega anterior', state='done', unresolved=True, reconciliation_required=True)]),
    ]:
        rows.append(dict(id=task, name=title, state='unknown', state_source='pane',
                         doing='harness state unavailable (unknown codex-unverified)',
                         blocked_by='previous' if deps else '-', execution={'harness': 'codex', 'source': 'task.meta'},
                         flow=dict(phase='dependency' if deps else 'unknown', state='waiting' if deps else 'unknown',
                                   source='current_state.pane', reason='harness state unavailable (unknown codex-unverified)',
                                   dependencies=deps, project='Deck',
                                   presentation={'summary': 'Revisando los resultados y las comprobaciones locales.'})))
    data.update(in_flight=rows, gates=[], decisions_open=[], landed=[],
                endpoints=[dict(id=r['id'], exists=True, agent='not_checked') for r in rows])
    snap = board.collector.snap
    f = bridge.fm
    snap.cols, snap.totals = f.make_cards(home, data, {}, {}, {}, snap.homes)
    board.collector._raw = {home.label: (data, time.time(), home.path)}
    return board, home
