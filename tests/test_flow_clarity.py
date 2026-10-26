"""Public UI contracts for workflow clarity, retained history and safe intake."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('clarity_fixture', HERE / 'test_flow_deliveries.py')
fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
f = fixture.flow


def install_explanation_endpoint(root):
    (root/'bin').mkdir(exist_ok=True)
    (root/'bin/fm-delivery-explain.sh').write_text('exec python3 "$FM_HOME/bin/endpoint.py" "$@"\n')
    (root/'bin/endpoint.py').write_text('''import json, os, sys
from pathlib import Path
args=sys.argv[1:];task=args[args.index('--task')+1];request=args[args.index('--request-id')+1]
root=Path(os.environ['FM_HOME']);log=root/'calls.jsonl'
with log.open('a') as out:out.write(json.dumps(args)+'\\n')
receipt=root/'request.json'
if '--status' not in args and not receipt.exists():receipt.write_text(json.dumps({'request_id':request,'task':task}))
print(json.dumps({'ok':True,'home':str(root),'task':task,'request_id':request,'state':'queued' if receipt.exists() else 'absent','receipt':'request.json'}))
''')




class ClarityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = fixture.fixture(self.root)
        self.env = patch.dict(os.environ, FM_FLOW_SHOW_LANDED='1', HERDR_PLUGIN_STATE_DIR=str(self.root/'plugin-state'))
        self.env.start(); self.addCleanup(self.env.stop)

    def ui(self, payload=None):
        snap = fixture.snapshot(self.home)
        if payload:
            snap.cols, snap.totals = f.make_cards(self.home, payload, {}, {}, {}, [self.home])
        ui = f.UI(); ui.collector.snapshot = lambda: snap
        return ui, snap

    def render(self, ui, snap, width=184, height=40):
        frames=[]
        with patch.object(f.shutil,'get_terminal_size',return_value=os.terminal_size((width,height))), patch.object(ui,'paint',side_effect=lambda rows,*_:frames.append(rows)):
            ui.render(snap,force=True)
        rows=[f._ANSI_RE.sub('',r) for r in frames[-1]]
        self.assertEqual(len(rows),height)
        self.assertTrue(all(f.display_width(r)<=width for r in rows))
        return '\n'.join(rows)

    def test_finished_work_stays_underway_and_full_reason_opens_with_enter(self):
        reason='Esperando la validación de la conexión '+('contexto completo '*40)+'FINAL_RAZON'
        (self.root/'state/task.status').write_text('paused [at=1792920000]: '+reason+'\n')
        ui,snap=self.ui({'in_flight':[{'id':'task','name':'Validar atención y enlace móvil sin perder el contexto','state':'paused'}, {'id':'ready','name':'Integración comprobada pendiente','state':'done'}]})
        self.assertEqual(len(snap.cols['underway']),2)
        self.assertFalse(snap.cols['awaiting_merge'])
        frame=self.render(ui,snap)
        self.assertNotIn('Awaiting Merge',frame)
        self.assertIn('Retención técnica',frame)
        self.assertIn('Integración pendiente',frame)
        ui.col_idx=1; f.parse_input(b'\r',ui)
        self.assertIsNotNone(ui.details)
        self.assertIn('FINAL_RAZON',str(f.UI.detail_fields(ui.details)))
        self.assertIn('FINAL_RAZON',self._all_details(ui,snap))

    def _all_details(self,ui,snap):
        text=self.render(ui,snap,60,18)
        while ui.details_scroll<ui.details_max:
            f.parse_input(b'\x1b[6~',ui); text+=self.render(ui,snap,60,18)
        return text

    def test_dependency_gets_name_owner_and_unknown_is_not_activity(self):
        with (self.root/'data/backlog.md').open('a') as out:
            out.write('## In flight\n- [ ] producer - Comprobar atención en el teléfono (kind: ship)\n')
        ui,snap=self.ui({'in_flight':[{'id':'task','name':'Limpieza de identidad','state':'unknown','blocked_by':'producer'}]})
        card=snap.cols['underway'][0]
        self.assertIn('Comprobar atención en el teléfono · fixture',f.dependency_caption(card))
        self.assertEqual(f.flow_context(card)['phase'],'dependency')
        card.dependencies[0]['reconciliation_required']=True
        self.assertIn('por conciliar',f.dependency_caption(card))
        card.blocked_by='';card.dependencies=[];card.live_status='working'
        self.assertEqual(f.flow_context(card)['phase'],'unknown')
        card.flow={'phase':'human','reason':'Elegir el alcance','waiting_for':'Respuesta de la persona','firstmate_action':'Coordinar opciones'}
        self.assertEqual(f.flow_context(card)['label'],'Espera respuesta humana')
        self.assertIn('Coordinar opciones',str(f.UI.detail_fields(card)))

    def test_small_terminal_keeps_selected_column_and_mouse_context(self):
        ui,snap=self.ui({'in_flight':[{'id':'task','name':'Atención pendiente','state':'paused','doing':'Esperar slot de validación'}]})
        ui.col_idx=1
        frame=self.render(ui,snap,60,18)
        self.assertIn('Underway',frame);self.assertIn('←/→ 2/4',frame)
        self.assertIn('Atención pendiente',frame)
        x,y,*_=ui.card_regions[0];ui.click(x+1,y+1,0)
        self.assertIsNotNone(ui.details)
        self.assertTrue(all(hit[2]<=60 for hit in ui.card_regions))

    def test_desktop_overview_and_narrow_default_find_work_without_empty_landing(self):
        ui,snap=self.ui({'in_flight':[{'id':'task','name':'Trabajo relevante','state':'paused'}]})
        desktop=self.render(ui,snap,126,46)
        self.assertIn('Charted Next',desktop)
        self.assertIn('Underway',desktop)
        self.assertIn('Trabajo relevante',desktop)
        ui.selection_auto=True;ui.col_idx=0
        narrow=self.render(ui,snap,60,18)
        self.assertEqual(ui.current_key(),'underway')
        self.assertIn('Trabajo relevante',narrow)
        self.assertIn('Next 0 · Work 1 · Calls 0',narrow)
        self.assertIn('Estado de Firstmate: paused',narrow)
        f.parse_input(b'\x1b[D',ui)
        self.assertIn('Charted Next',self.render(ui,snap,60,18))
        self.assertEqual(ui.current_key(),'charted')

    def test_delivery_summary_is_separate_readable_and_bounded(self):
        (self.root/'data/old/delivery.md').write_text('# Entrega\n\n## Resultado\n**Skill instalada** para conexión local.\n\n## Por qué\nEvita pedir ayuda antes de intentar Wi-Fi.\n\n## Verificado\nPruebas dirigidas pasaron.\n\n## Pendiente\nLa aceptación móvil sigue pendiente.\n[Informe](report.md)\n[Receipt](receipt.json)\n')
        ui,snap=self.ui();ui.open_delivery(snap.cols['landed'][0])
        text=self.render(ui,snap,126,46)
        self.assertIn('Skill instalada',text);self.assertIn('Pedir explicación a Firstmate',text)
        self.assertNotIn('END_OF_REPORT',ui.delivery)
        self.assertNotIn('##',ui.delivery);self.assertNotIn('**',ui.delivery);self.assertNotIn('](report.md)',ui.delivery)
        self.assertIn('sólo fecha; hora no registrada',text)
        f.parse_input(b'r',ui);self.assertIn('END_OF_REPORT',ui.delivery)
        f.parse_input(b'd',ui);self.assertIn('RECEIPT_END',ui.delivery)
        (self.root/'data/old/delivery.md').write_text('# Entrega\n## Resultado\nResultado conservado.\n\n'+('Datos técnicos.\n'*500)+'FINAL_RESUMEN')
        ui.open_delivery(snap.cols['landed'][0])
        self.assertLess(len(ui.delivery),1200)
        self.assertIn('abreviado',ui.delivery)
        ui.delivery_set_view('details');self.assertIn('FINAL_RESUMEN',ui.delivery)

    def test_technical_receipt_does_not_become_the_initial_delivery_brief(self):
        receipt='Ready in branch fm/example; HEAD abc123.\n\n'+('Technical transport receipt.\n'*200)
        (self.root/'data/old/delivery.md').write_text(receipt)
        ui,snap=self.ui();ui.open_delivery(snap.cols['landed'][0])
        self.assertIn('no un resumen breve',ui.delivery)
        self.assertNotIn('Ready in branch',ui.delivery)
        self.assertNotIn('Technical transport',ui.delivery)
        ui.delivery_set_view('details')
        self.assertIn('Ready in branch',ui.delivery)
        self.assertIn('Technical transport',ui.delivery)

    def test_timestamp_refresh_does_not_create_precision_or_model_activity(self):
        event=f.CardTime('Delivered','2026-10-25T14:00:00+00:00')
        a=f.local_time_caption(event,event.epoch+120);b=f.local_time_caption(event,event.epoch+180)
        self.assertIn('hace 2 min',a);self.assertIn('hace 3 min',b)
        self.assertIn('2026-10-25',a)
        self.assertNotIn('00:00',f.local_time_caption(f.CardTime('Delivered','2026-10-25')))
        self.assertIn('no registradas',f.local_time_caption(f.CardTime('Delivered')))
        with patch.object(f.time,'time',return_value=event.epoch+30):
            fresh=f.supervisor_caption({'state':'coordinating','summary':'Integrar los dos frentes','observed_at':event.epoch,'ttl':60})
        with patch.object(f.time,'time',return_value=event.epoch+61):
            stale=f.supervisor_caption({'state':'coordinating','summary':'Integrar los dos frentes','observed_at':event.epoch,'ttl':60})
        self.assertIn('Actividad del modelo desconocida',fresh)
        self.assertIn('declaración vencida',stale)

    def endpoint(self):
        install_explanation_endpoint(self.root)

    def test_explanation_is_identity_only_deduplicated_and_status_is_read_only(self):
        self.endpoint();ui,snap=self.ui();ui.open_delivery(snap.cols['landed'][0])
        self.assertEqual(ui.explanation['state'],'absent');self.assertFalse((self.root/'request.json').exists())
        # Keyboard focus and Enter are equivalent to the visible action.
        ui.delivery_action_focus=4;f.parse_input(b'\r',ui)
        self.assertEqual(ui.explanation['state'],'queued')
        frame=self.render(ui,snap,60,18);self.assertIn('En cola para Firstmate',frame)
        self.assertIn('fixture · old',frame)
        hit=next(hit for hit in ui.reader_hits if hit[3]=='explain');ui.click(hit[0],hit[2],0)
        ui.open_delivery(snap.cols['landed'][0]);f.parse_input(b'e',ui)
        calls=[json.loads(line) for line in (self.root/'calls.jsonl').read_text().splitlines()]
        self.assertEqual(sum('--status' not in args for args in calls),1)
        self.assertEqual(len({args[args.index('--request-id')+1] for args in calls}),1)
        self.assertTrue(all('--task' in args and 'old' in args and '--json' in args for args in calls))
        self.assertNotIn('END_OF_REPORT',str(calls))
        self.assertEqual(f.delivery_records(self.home)[0][0]['id'],'recent')

    def test_explanation_missing_backend_and_wrong_home_are_visible(self):
        ui,snap=self.ui();ui.open_delivery(snap.cols['landed'][0]);f.parse_input(b'e',ui)
        self.assertEqual(ui.explanation['state'],'unsupported')
        self.assertIn('backend',self.render(ui,snap,60,18))
        self.endpoint()
        with patch.object(f.subprocess,'run',return_value=type('Result',(),{'returncode':0,'stdout':json.dumps({'ok':True,'home':'/wrong','task':'old','request_id':'wrong','state':'queued'})})()):
            result=f.explanation_request(self.home,'old')
        self.assertEqual(result['state'],'error')
        self.assertEqual(f.explanation_request(self.home,'../unsafe')['state'],'unsupported')

    def test_full_producer_title_and_supervisor_declaration_survive_projection(self):
        event=f.CardTime('Declared','2026-10-25T14:00:00+00:00')
        ui,snap=self.ui({'supervisor':{'state':'coordinating','summary':'Unir los cambios de Deck y Firstmate',
                                     'observed_at':event.epoch,'ttl':300,'freshness':'fresh','source':'supervisor.declaration'},
                         'in_flight':[{'id':'task','name':'Título recortado…','state':'unknown',
                                       'flow':{'title':'Título completo con motivo y FINAL_TITULO','phase':'active','state':'unknown',
                                               'reason':'Declaración histórica; falta confirmación actual'}}]})
        card=snap.cols['underway'][0]
        self.assertIn('FINAL_TITULO',card.title)
        card.run_elapsed,card.run_tokens='10m','20k'
        self.assertIn('20k tokens',str(f.UI.detail_fields(card)))
        self.assertEqual(f.flow_context(card)['phase'],'unknown')
        with patch.object(f.time,'time',return_value=event.epoch+60):
            self.assertIn('Unir los cambios',str(f.UI.detail_fields(card)))
            self.assertIn('Actividad del modelo desconocida',str(f.UI.detail_fields(card)))
        with patch.object(f.time,'time',return_value=event.epoch+301):
            self.assertIn('declaración vencida',str(f.UI.detail_fields(card)))

    def test_installed_intake_partial_exit_retains_saved_unannounced_receipt(self):
        self.endpoint()
        request='deck-explain-'+f.delivery_key(f.delivery_home_identity(self.home),'old')[:32]
        payload={'ok':True,'request_id':request,'home':str(self.root),'task':'old',
                 'state':'saved_unannounced','message':'Guardada; aviso no confirmado.', 'receipt':{'id':'informational-note'}}
        result=type('Result',(),{'returncode':3,'stdout':json.dumps(payload)})()
        with patch.object(f.subprocess,'run',return_value=result):
            response=f.explanation_request(self.home,'old')
        self.assertEqual(response['state'],'saved_unannounced')
        self.assertEqual(response['receipt'],payload['receipt'])
        payload['state']='queued';result.stdout=json.dumps(payload)
        with patch.object(f.subprocess,'run',return_value=result):
            self.assertEqual(f.explanation_request(self.home,'old')['state'],'error')

    def test_public_history_omission_preserves_reopened_current_record_check(self):
        (self.root/'bin').mkdir()
        (self.root/'bin/fm-delivery-history.sh').write_text('cat "$FM_HOME/history.json"\n')
        (self.root/'history.json').write_text(json.dumps({'schema':'fm-delivery-history.v1','home':str(self.root),
                                                        'complete':True,'sources':[],'retention':{},'records':[]}))
        (self.root/'data/backlog.md').write_text('## In flight\n- [ ] old - Tarea reabierta (kind: ship)\n')
        ui,snap=self.ui();ui.open_delivery(snap.cols['landed'][0])
        self.assertIn('se reabrió',ui.delivery)
        self.assertIn('histórica',ui.delivery)
        ui.delivery_set_view('details')
        self.assertIn('State: in_flight',ui.delivery)
        self.assertIn('Tarea reabierta',ui.delivery)

    def test_main_dependency_owner_is_readable_instead_of_a_transport_marker(self):
        ui,snap=self.ui({'in_flight':[{'id':'task','name':'Limpieza','state':'unknown',
                                     'flow':{'phase':'dependency','dependencies':[{'id':'notify','title':'Prueba móvil','owner':'(main)','state':'in_flight'}]}}]})
        self.assertEqual(f.dependency_caption(snap.cols['underway'][0]),'Prueba móvil · fixture')

    def test_public_history_preserves_precise_event_and_rejects_foreign_identity(self):
        (self.root/'bin').mkdir()
        (self.root/'bin/fm-delivery-history.sh').write_text('cat "$FM_HOME/history.json"\n')
        event={'kind':'delivered','value':'2026-10-21T14:30:15+00:00','precision':'second','source':'backlog.owner.done'}
        data={'schema':'fm-delivery-history.v1','home':str(self.root),'complete':True,
              'sources':['data/done-archive.md'],'retention':{'done_keep':10},
              'records':[{'id':'old','title':'Entrega pública','state':'done','source':'archive',
                          'completion':{'date':'2026-10-21','verb':'done'},'card_time':event}]}
        (self.root/'history.json').write_text(json.dumps(data))
        rows,note=f.delivery_records(self.home)
        self.assertEqual(rows[0]['closed'],'2026-10-21')
        self.assertEqual(rows[0]['card_time'],event)
        self.assertIn('done_keep',note)
        ui,snap=self.ui();ui.open_delivery(snap.cols['landed'][0])
        self.assertIsNotNone(ui.delivery_event.epoch)
        self.assertEqual(ui.delivery_event.source,'backlog.owner.done')
        data['home']='/foreign';(self.root/'history.json').write_text(json.dumps(data))
        rows,note=f.delivery_records(self.home)
        self.assertNotIn('Entrega pública',[row['title'] for row in rows])
        self.assertIn('export unavailable',note)

    def test_history_search_terms_find_archived_work_without_changing_retention(self):
        archive=self.root/'data/done-archive.md'
        with archive.open('a') as out:out.write('- [x] research - Research: investigación de videojuegos en Demo research data/research/report.md (kind: scout) (reported 2026-10-23)\n')
        before=archive.read_bytes();ui,snap=self.ui();ui.open_history()
        ui.history['query']='demo investigacion'
        self.assertEqual([r['id'] for r in ui.history_rows()],['research'])
        self.assertIn('done_keep=10',ui.history['note'])
        self.assertEqual(before,archive.read_bytes())


if __name__=='__main__':unittest.main()
