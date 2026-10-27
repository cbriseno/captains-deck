"""Human context through the board/ticket interfaces, with isolated producers."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import fixture, bridge


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, HERDR_PLUGIN_STATE_DIR=str(self.root / 'state'))
        env.start()
        self.addCleanup(env.stop)
        self.board, self.home = fixture(self.root)

    def task(self, name='web'):
        return next(c for c in self.board.collector.snap.cols['underway'] if c.task == name)

    def read(self, card):
        return self.board.ticket(self.board.key(card))

    def test_home_coordinator_and_registered_executor_are_separate(self):
        card = self.task()
        card.agent = 'codex'
        card.snapshot_state = 'working'
        card.live_status = 'done'
        result = self.read(card)
        p = result['presentation']
        self.assertEqual(result['owner'], self.home.label)
        self.assertEqual(p['executor'], {'harness': 'codex'})
        self.assertNotIn('evidence', result)
        self.assertNotEqual(result['owner'], p['executor']['harness'])
        self.assertEqual(result['native']['herdr'], 'done')

    def test_validation_job_does_not_prove_native_worker_activity(self):
        card = self.task()
        card.flow.update(reason='validating (running) · run: SANITIZED_RUN',
                         next_action='El worker continúa el encargo.')
        p = self.read(card)['presentation']
        self.assertEqual(p['run_step'], {'phase': 'validation', 'state': 'running'})
        self.assertEqual(p['summary'], '')
        self.assertEqual(p['next_action'], '')

    def test_missing_conditions_and_stock_actions_stay_missing(self):
        card = self.task()
        card.flow = {'phase': 'technical', 'state': 'waiting',
                     'reason': 'harness state unavailable (unknown codex-unverified)',
                     'waiting_for': 'revisión de la condición técnica declarada',
                     'next_action': 'Firstmate comprueba el bloqueo o la pausa; el texto declarado no demuestra que haya desaparecido.'}
        result = self.read(card)
        for key in ('summary', 'waiting_for', 'next_action'):
            self.assertEqual(result['presentation'][key], '')
        self.assertEqual(result['presentation']['executor']['harness'], '')

    def test_stock_authored_phrases_fall_back_to_concrete_recorded_facts(self):
        card = self.task()
        card.flow.update(reason='Acceptance is pending.', waiting_for='Reviewer sign-off.',
                         next_action='Enviar el PR #12')
        card.flow['presentation'] = {'summary': 'Motivo no registrado',
                                     'waiting_for': 'verificación e integración de Firstmate',
                                     'next_action': 'El worker continúa el encargo.'}
        p = self.read(card)['presentation']
        self.assertEqual(p['summary'], 'Acceptance is pending.')
        self.assertEqual(p['waiting_for'], 'Reviewer sign-off.')
        self.assertEqual(p['next_action'], 'Enviar el PR #12')

    def test_unusable_authored_text_falls_back_to_recorded_facts(self):
        card = self.task()
        card.flow.update(reason='Acceptance is pending.', waiting_for='Reviewer sign-off.',
                         next_action='Enviar el PR #12')
        card.flow['presentation'] = {'summary': ' ',
                                     'waiting_for': 'Revisa /home/example/review/report.md',
                                     'next_action': 'Revisa /home/example/review/report.md'}
        p = self.read(card)['presentation']
        self.assertEqual(p['summary'], 'Acceptance is pending.')
        self.assertEqual(p['waiting_for'], 'Reviewer sign-off.')
        self.assertEqual(p['next_action'], 'Enviar el PR #12')

    def test_whitespace_padded_stock_phrase_does_not_leak_as_next_action(self):
        card = self.task()
        card.flow.update(next_action='Enviar el PR #12')
        card.flow['presentation'] = {'next_action': 'El worker continúa el encargo.\n'}
        self.assertEqual(self.read(card)['presentation']['next_action'], 'Enviar el PR #12')

    def test_authored_summary_wait_and_action_survive_localization_without_mutation(self):
        card = self.task('dependent')
        card.flow['presentation'] = {'summary': 'The adapter is ready; physical acceptance remains open.',
                                     'waiting_for': 'The scheduled device test completes.',
                                     'next_action': 'Record its result and keep any limitations visible.'}
        before = copy.deepcopy(card.flow)
        english = self.read(card)
        spanish = bridge.Board(self.board.data_dir, collector=self.board.collector, language='es-MX').ticket(self.board.key(card))
        self.assertEqual(english['presentation'], spanish['presentation'])
        self.assertEqual(english['presentation']['summary'], before['presentation']['summary'])
        self.assertEqual(card.flow, before)
        self.assertTrue(english['dependencies'][0]['unresolved'])

    def test_long_reports_are_bounded_and_original_remains_available(self):
        card = self.task()
        card.flow['reason'] = 'The interface is ready. ' + 'Acceptance remains open. ' * 40
        result = self.read(card)
        self.assertEqual(result['presentation']['summary'], 'The interface is ready.')

    def test_human_opening_survives_later_technical_evidence(self):
        card = self.task()
        card.flow['reason'] = 'The change was applied. Evidence is in report.json and run: SANITIZED.'
        result = self.read(card)
        self.assertEqual(result['presentation']['summary'], 'The change was applied.')

    def test_card_report_text_is_the_complete_declared_reason(self):
        card = self.task()
        report = 'Informe completo.\n\n' + 'Detalle de evidencia sintética. ' * 40 + 'FIN_DEL_INFORME'
        card.flow.update(declared_state='working', reason=report)
        self.assertEqual(self.board.card_json(card)['report']['text'], report)

    def test_readable_sentences_keep_their_filenames(self):
        card = self.task()
        card.flow['reason'] = 'Actualicé README.md con el flujo nuevo.\n\nDetalle.'
        self.assertEqual(self.read(card)['presentation']['summary'], 'Actualicé README.md con el flujo nuevo.')
        card.flow['reason'] = 'Se actualizó la guía en docs/guide.md. Todo verde.'
        self.assertEqual(self.read(card)['presentation']['summary'], 'Se actualizó la guía en docs/guide.md. Todo verde.')
        card.flow['next_action'] = 'Revisa docs/guide.md antes de integrar.'
        self.assertEqual(self.read(card)['presentation']['next_action'], 'Revisa docs/guide.md antes de integrar.')

    def test_readable_sentences_keep_schema_and_head_words(self):
        card = self.task()
        for sentence in ('Added schema validation for the config file.',
                         'Migrated the database schema to v2. Tests pass.',
                         'The release branch head now includes the fix.',
                         'Añadí validación del schema de configuración.',
                         'Updated the schema: added a users table.',
                         'Schema: new users table added.',
                         'The new head: Ana leads the release.',
                         'Actualicé el schema: agregué una tabla de usuarios.',
                         'Nuevo head: Ana coordina la entrega.'):
            card.flow.update(reason=sentence, waiting_for=sentence, next_action=sentence)
            p = self.read(card)['presentation']
            self.assertEqual((p['summary'], p['waiting_for'], p['next_action']), (sentence,) * 3)
        landed = next(c for c in self.board.collector.snap.cols['landed'] if c.task == 'demo-research')
        landed.delivery_summary = 'Added schema validation for the config file.\n\nDetalle.'
        self.assertEqual(self.board.card_json(landed)['summary'], 'Added schema validation for the config file.')

    def test_schema_and_head_tokens_fall_back_to_usable_text(self):
        card = self.task()
        for candidate in ('HEAD abc123', 'head: abc123', 'schema: fm-flow.v1', '{"schema":"fm-flow.v1"}',
                          '{"schema": "fm-flow.v1", "task": "sample"}', 'schema: fm-flow.v1 state: running'):
            card.flow.update(reason=candidate, waiting_for=candidate, next_action=candidate)
            p = self.read(card)['presentation']
            self.assertEqual((p['summary'], p['waiting_for'], p['next_action']), ('', '', ''), candidate)

    def test_purely_technical_filenames_fall_back_to_usable_text(self):
        card = self.task()
        for candidate in ('report.json', 'report.json.', 'report.json logs.jsonl'):
            card.flow['reason'] = candidate
            card.flow['waiting_for'] = candidate
            card.flow['next_action'] = 'run: SANITIZED_RUN'
            p = self.read(card)['presentation']
            self.assertEqual(p['summary'], '')
            self.assertEqual(p['waiting_for'], '')
            self.assertEqual(p['next_action'], '')

    def test_landed_card_summary_keeps_a_filename_sentence(self):
        landed = next(c for c in self.board.collector.snap.cols['landed'] if c.task == 'demo-research')
        landed.delivery_summary = 'Actualicé README.md con el flujo nuevo.\n\nDetalle.'
        self.assertEqual(self.board.card_json(landed)['summary'], 'Actualicé README.md con el flujo nuevo.')

    def test_malformed_optional_presentation_fields_do_not_break_board_reads(self):
        card = self.task()
        card.flow['presentation'] = {'summary': [], 'next_action': {}, 'waiting_for': 123}
        result = self.read(card)
        self.assertEqual(result['presentation']['summary'], card.flow['reason'])
        self.assertEqual(result['presentation']['next_action'], card.flow['next_action'])

    def test_all_column_types_keep_their_event_precision(self):
        board = self.board.board()
        self.assertEqual([c['id'] for c in board['columns']], ['charted', 'underway', 'captains_call', 'landed'])
        for column in board['columns']:
            for card in column['cards']:
                self.assertIn('presentation', card)
        queued = board['columns'][0]['cards'][0]
        self.assertEqual(queued['time']['date'], '2026-10-24')
        self.assertIsNone(queued['time']['epoch'])
        card = self.board.collector.snap.cols['captains_call'][0]
        card.answered_hidden = True
        self.assertTrue(self.read(card)['answered'])
        self.assertFalse((Path(self.home.path) / 'mutations.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
