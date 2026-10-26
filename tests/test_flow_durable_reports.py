"""Durable declarations explain work while native Codex activity is unknown."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('durable_flow', ROOT / 'scripts/flow_tui.py')
f = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = f
spec.loader.exec_module(f)


class DurableReportsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'state').mkdir()
        self.home = f.Home('Firstmate', str(self.root), 'captain')

    def card(self, flow=None, status='', **entry):
        (self.root / 'state/task.status').write_text(status)
        row = dict(id='task', name='Código integrado; aceptación pendiente', state='unknown',
                   doing='harness state unavailable (unknown codex-unverified)', flow=flow or {})
        row.update(entry)
        cols, _ = f.make_cards(self.home, {'in_flight': [row], 'generated': '2099-01-01T00:00:00Z'},
                               f.build_meta_index([self.home]), {}, {}, [self.home])
        self.assertEqual(len(cols['underway']), 1)
        self.assertFalse(cols['awaiting_merge'])
        return cols['underway'][0]

    def test_exported_unknown_does_not_mask_pause_or_scoped_done(self):
        for state, phase in [('paused', 'waiting'), ('parked', 'waiting'), ('done', 'reported'),
                             ('working', 'reported'), ('blocked', 'technical')]:
            with self.subTest(state=state):
                card = self.card({'phase': 'unknown', 'state': 'unknown', 'declared_state': state,
                                  'reason': 'Implementación integrada; falta aceptación nativa de Root.',
                                  'source': 'status.declaration (historical)'})
                context = f.flow_context(card)
                self.assertEqual(context['phase'], phase)
                self.assertIn('Root', context['reason'])
                self.assertEqual(context['native_status'], 'Estado nativo desconocido')
                self.assertEqual(card.snapshot_state, 'unknown')
                self.assertNotIn('integra', context['next_action'])
                self.assertNotIn('Actividad', context['label'])

    def test_full_wait_with_coordination_prose_keeps_state_age_and_owner(self):
        text = 'inbox069 atendido; faltan controles PC/móvil; Root coordina la aceptación. ' + 'evidencia ' * 40
        card = self.card(status='paused [key=validation] [at=1792940664]: ' + text + '\n')
        state, report, event = f.durable_report(card)
        self.assertEqual((state, report, event.epoch), ('paused', text.strip(), 1792940664))
        context = f.flow_context(card)
        self.assertEqual(context['phase'], 'waiting')
        self.assertIn('evidencia ' * 30, context['reason'])
        details = dict(f.UI.detail_fields(card))
        self.assertIn('Firstmate', details['Owner'])
        self.assertIn('2026-10-25', details['Report time'])
        for width in (32, 60, 126):
            rows = [f._ANSI_RE.sub('', row) for row in f.UI().render_card(card, width, False)]
            self.assertIn('Espera declarada', '\n'.join(rows))
            self.assertIn('Firstmate', '\n'.join(rows))
            self.assertTrue(all(f.display_width(row) <= width for row in rows))

    def test_report_timestamp_requires_declaration_provenance(self):
        flow = {'phase': 'unknown', 'declared_state': 'done', 'reason': 'Seguimiento entregado; falta aceptación Root.'}
        card = self.card(flow)
        self.assertIsNone(f.durable_report(card)[2].epoch)
        self.assertNotIn('2099', str(f.UI.detail_fields(card)))
        card = self.card(flow, card_time={'kind': 'updated', 'value': '2026-10-25T09:52:18Z',
                                         'precision': 'second', 'source': 'status.declaration.at'})
        self.assertEqual(f.durable_report(card)[2].epoch, 1792921938)
        card = self.card(flow, card_time={'kind': 'updated', 'value': '2026-10-25T09:52:18Z',
                                         'precision': 'second', 'source': 'poll.observation'})
        self.assertIsNone(f.durable_report(card)[2].epoch)

    def test_resolution_and_new_attempt_do_not_resurrect_an_old_wait(self):
        old = 'paused [at=1792788000]: Esperar validación\n'
        card = self.card(status=old + 'resolved [at=1792788100]: Condición resuelta\n')
        self.assertEqual(f.flow_context(card)['phase'], 'unknown')
        (self.root / 'state/task.meta').write_text('spawn_gen=s1792788200.123.1\n')
        card = self.card(status=old)
        self.assertEqual(f.durable_report(card)[0], '')
        self.assertNotIn('Esperar validación', f.flow_context(card)['reason'])

    def test_explicit_dependency_and_supported_integration_keep_their_meaning(self):
        card = self.card({'phase': 'dependency', 'declared_state': 'paused', 'reason': 'Retención operativa',
                          'waiting_for': 'Root conserva el worktree', 'next_action': 'Mantener la retención.'})
        self.assertEqual(f.flow_context(card)['phase'], 'dependency')
        self.assertEqual(f.flow_context(card)['next_action'], 'Mantener la retención.')
        card = self.card({'phase': 'integration', 'state': 'ready', 'reason': 'Rama por integrar.'},
                         state='done', doing='Ready in branch')
        self.assertEqual(f.flow_context(card)['label'], 'Lista para integración')

    def test_current_confirmed_work_supersedes_historical_wait_in_summary(self):
        card = self.card(status='paused [at=1792788000]: Esperar validación\n',
                         state='working', doing='validating current build')
        self.assertEqual(f.flow_context(card)['phase'], 'active')
        # The durable wait remains evidence, while current activity is separate.
        self.assertIn('validating current build', f.flow_context(card)['reason'])

    def test_unknown_observation_fallback_is_not_a_durable_reports_next_action(self):
        fallback = 'Firstmate necesita una observación actual; la última declaración se conserva como contexto.'
        for declared in ('paused', 'done', 'working'):
            with self.subTest(declared=declared):
                flow = {'phase': 'unknown', 'state': 'unknown', 'declared_state': declared,
                        'reason': 'Código integrado; Root coordina la aceptación pendiente.',
                        'next_action': fallback}
                card = self.card(flow)
                context = f.flow_context(card)
                self.assertNotEqual(context['next_action'], fallback)
                self.assertIn('Firstmate', context['next_action'])
                self.assertEqual(context['native_status'], 'Estado nativo desconocido')
                self.assertIn('codex-unverified', dict(f.UI.detail_fields(card))['Firstmate'])
                self.assertEqual(card.flow['next_action'], fallback)  # source evidence is untouched
                # A concrete action in the same unknown-native contract wins.
                flow['next_action'] = 'Root coordina una ventana de aceptación.'
                card = self.card(flow)
                self.assertEqual(f.flow_context(card)['next_action'], flow['next_action'])
        dependency = {'phase': 'unknown', 'declared_state': 'paused', 'reason': 'Esperar dependencia',
                      'next_action': fallback, 'waiting_for': 'Root conserva la sesión protegida'}
        card = self.card(dependency, blocked_by='owned-dependency')
        context = f.flow_context(card)
        self.assertEqual(context['phase'], 'dependency')
        self.assertEqual(context['waiting_for'], dependency['waiting_for'])
        self.assertNotEqual(context['next_action'], fallback)
        # Without a report the original unknown diagnostic still applies.
        card = self.card({'phase': 'unknown', 'next_action': fallback})
        self.assertEqual(f.flow_context(card)['next_action'], fallback)


if __name__ == '__main__':
    unittest.main()
