"""Exercise the HTTP trust boundary and actual temporary intake processes."""
from concurrent.futures import ThreadPoolExecutor
import copy
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from fixtures import fixture, bridge


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,HERDR_PLUGIN_STATE_DIR=str(self.root/'state'))
        self.env.start();self.addCleanup(self.env.stop)
        self.board,self.home=fixture(self.root)
        self.server=bridge.Server(('127.0.0.1',0),self.board,bridge.Security(8787))
        self.port=self.server.server_port
        self.server.security=bridge.Security(self.port,'https://deck.example.ts.net:8446',['captain@example.com'])
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)
        self.cookie='';self.csrf=''

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)

    def req(self,path,body=None,headers=None,method=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=10)
        h={'Host':f'127.0.0.1:{self.port}'}
        if self.cookie:h['Cookie']=self.cookie
        if body is not None:h.update({'Origin':f'http://127.0.0.1:{self.port}','Content-Type':'application/json','X-Deck-CSRF':self.csrf})
        h.update(headers or {})
        connection.request(method or ('POST' if body is not None else 'GET'),path,json.dumps(body) if body is not None else None,h)
        response=connection.getresponse();data=response.read();status=response.status
        cookie=response.getheader('Set-Cookie')
        if cookie:self.cookie=cookie.split(';')[0]
        connection.close()
        try:data=json.loads(data)
        except ValueError:pass
        return status,data

    def init(self):
        status,board=self.req('/api/board');self.assertEqual(status,200)
        self.csrf=board['csrf'];return board

    def call(self):
        board=self.init();key=next(c for c in board['columns'] if c['id']=='captains_call')['cards'][0]['key']
        status,ticket=self.req('/api/ticket/'+key);self.assertEqual(status,200)
        return key,dict(value='scope',note='Mi respuesta real de prueba',occurrence=ticket['call']['occurrence'],request_id='a'*32)

    def test_board_serializes_model_time_four_columns_and_does_not_mutate(self):
        board=self.init()
        self.assertEqual([c['id'] for c in board['columns']],['charted','underway','captains_call','landed'])
        self.assertEqual(len(board['columns'][1]['cards']),3)
        self.assertEqual(board['columns'][1]['cards'][2]['context']['phase'],'integration')
        self.assertFalse((Path(self.home.path)/'mutations.jsonl').exists())
        self.assertTrue(all('home_path' not in c for col in board['columns'] for c in col['cards']))

    def test_durable_reports_survive_unknown_native_on_board_and_ticket(self):
        f = bridge.fm
        rows = []
        for state in ('paused', 'done'):
            rows.append({'id': state, 'name': 'Integrado; aceptación pendiente', 'state': 'unknown',
                         'doing': 'harness state unavailable (unknown codex-unverified)',
                         'flow': {'phase': 'unknown', 'state': 'unknown', 'declared_state': state,
                                  'reason': 'Root debe completar la aceptación; el código ya está integrado.',
                                  'source': 'status.declaration (historical)'},
                         'card_time': {'kind': 'updated', 'value': '2026-10-25T09:52:18Z',
                                       'precision': 'second', 'source': 'status.declaration.at'}})
        self.board.collector.snap.cols, _ = f.make_cards(self.home, {'in_flight': rows}, {}, {}, {}, [self.home])
        cards = self.init()['columns'][1]['cards']
        self.assertEqual([c['context']['phase'] for c in cards], ['waiting', 'reported'])
        for card in cards:
            self.assertEqual(card['report']['time']['epoch'], 1792921938)
            self.assertEqual(card['native']['state'], 'unknown')
            self.assertIn('codex-unverified', card['native']['detail'])
            self.assertEqual(card['context']['native_status'], 'Unknown native state')
            status, ticket = self.req('/api/ticket/' + card['key'])
            self.assertEqual(status, 200)
            self.assertEqual(ticket['report'], card['report'])
            self.assertEqual(ticket['context'], card['context'])
        self.assertFalse((Path(self.home.path) / 'mutations.jsonl').exists())

    def test_fail_closed_host_identity_origin_csrf_and_content_type(self):
        key,body=self.call();path='/api/ticket/'+key+'/answer'
        for headers in [{'Host':'evil.example'},{'Origin':'https://evil.example'}, {'X-Deck-CSRF':'bad'}, {'Content-Type':'text/plain'}, {'Origin':''}, {'Sec-Fetch-Site':'cross-site'}]:
            self.assertEqual(self.req(path,body,headers)[0],403)
        self.assertEqual(self.req('/api/board',headers={'Host':'deck.example.ts.net:8446'})[0],403)
        self.assertEqual(self.req('/api/board',headers={'Host':'deck.example.ts.net:8446','Tailscale-User-Login':'intruder@example.com'})[0],403)
        self.assertEqual(self.req('/api/board',headers={'Host':'deck.example.ts.net:8446','Tailscale-User-Login':'captain@example.com'})[0],200)
        self.assertFalse((Path(self.home.path)/'mutations.jsonl').exists())

    def test_initial_cross_site_document_entry_establishes_guarded_session(self):
        # A clicked dashboard/notification link can retain cross-site through
        # redirects. It is a document entry, not a cross-site API request.
        identity={'Host':'deck.example.ts.net:8446','Tailscale-User-Login':'captain@example.com'}
        navigation={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate',
                    'Sec-Fetch-Dest':'document','Sec-Fetch-User':'?1'}
        for path in ['/', '/?home=opaque&task=ask&view=call', '/index.html']:
            with self.subTest(path=path):
                self.cookie=''
                status,html=self.req(path,headers=dict(identity,**navigation))
                self.assertEqual(status,200)
                self.assertIn(b'<html',html)
                self.assertTrue(self.cookie.startswith('deck_session='))
                same_origin=dict(identity,**{'Sec-Fetch-Site':'same-origin'})
                status,board=self.req('/api/board',headers=same_origin)
                self.assertEqual(status,200)
                self.assertTrue(board['csrf'])
                self.assertEqual(self.req('/app.js',headers=same_origin)[0],200)
        self.assertFalse((Path(self.home.path)/'mutations.jsonl').exists())

    def test_cross_site_entry_exception_does_not_allow_fetch_frames_or_actions(self):
        navigation={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate',
                    'Sec-Fetch-Dest':'document','Sec-Fetch-User':'?1'}
        for changed in [{'Sec-Fetch-Mode':'cors','Sec-Fetch-Dest':'empty'},
                        {'Sec-Fetch-Mode':'no-cors','Sec-Fetch-Dest':'image'},
                        {'Sec-Fetch-Dest':'iframe'}, {'Sec-Fetch-Dest':'object'},
                        {'Sec-Fetch-Mode':''}, {'Sec-Fetch-Dest':''},
                        {'Sec-Fetch-User':''}, {'Sec-Fetch-User':'?0'},
                        {'Host':'evil.example'}, {'Origin':'https://evil.example'},
                        {'Host':'deck.example.ts.net:8446'},
                        {'Host':'deck.example.ts.net:8446','Tailscale-User-Login':'intruder@example.com'}]:
            with self.subTest(changed=changed):
                self.assertEqual(self.req('/',headers=dict(navigation,**changed))[0],403)
        for path in ['/api/board','/api/history','/api/receipts','/health','/app.js']:
            with self.subTest(path=path):
                self.assertEqual(self.req(path,headers=navigation)[0],403)
        key,body=self.call()
        for path,payload in [('/',{}),('/api/ticket/'+key+'/answer',body),
                             ('/api/delivery/'+bridge.home_id(self.home)+'/demo-research/explanation',{})]:
            with self.subTest(post=path):
                # Even otherwise valid Origin/JSON/session/CSRF cannot turn
                # cross-site POST into a navigation exemption.
                self.assertEqual(self.req(path,payload,navigation)[0],403)
        self.assertFalse((Path(self.home.path)/'mutations.jsonl').exists())
        self.assertFalse((Path(self.home.path)/'explain-demo-research').exists())

    def test_real_temporary_intake_race_and_replay_across_restart(self):
        key,body=self.call()
        with ThreadPoolExecutor(2) as pool:
            replies=list(pool.map(lambda _:self.board.submit(key,body,'local'),range(2)))
        self.assertTrue(all(r['saved'] for r in replies))
        self.assertEqual(len((Path(self.home.path)/'mutations.jsonl').read_text().splitlines()),1)
        self.assertEqual(replies[0]['ack'],'unknown');self.assertEqual(replies[0]['consumed'],'unknown')
        restarted=bridge.Board(self.board.data_dir,collector=self.board.collector)
        self.assertTrue(restarted.submit(key,body,'local')['replay'])
        with self.assertRaises(bridge.Refusal):restarted.submit(key,dict(body,note='otra respuesta'),'local')
        self.assertEqual(len((Path(self.home.path)/'mutations.jsonl').read_text().splitlines()),1)

    def test_stale_occurrence_option_change_and_missing_capability_never_submit(self):
        key,body=self.call()
        (Path(self.home.path)/'identity').write_text('2026-10-25T16:00:00Z#1')
        self.assertEqual(self.req('/api/ticket/'+key+'/answer',body)[0],409)
        self.assertFalse((Path(self.home.path)/'mutations.jsonl').exists())
        (Path(self.home.path)/'identity').write_text('2026-10-25T15:00:00Z#0')
        path=Path(self.home.path)/'state/decision-cards/ask.json'
        data=json.loads(path.read_text());data['card']['about']='Nueva pregunta';path.write_text(json.dumps(data))
        self.assertEqual(self.req('/api/ticket/'+key+'/answer',body)[0],409)
        script=Path(self.home.path)/'bin/intake.py';script.write_text(script.read_text().replace("'fm-captain-hold.capabilities.v1'","'unsupported'"))
        _,ticket=self.req('/api/ticket/'+key);self.assertTrue(ticket['call']['error'])

    def test_current_owner_identity_two_same_named_tasks_and_no_aggregate_routing(self):
        second,home2=fixture(self.root/'second')
        home2.label='Otro'
        self.board.collector.snap.homes.append(home2)
        other=second.collector.snap.cols['captains_call'][0]
        self.board.collector.snap.cols['captains_call'].append(other)
        board=self.init();cards=board['columns'][2]['cards']
        self.assertEqual(len(set(c['key'] for c in cards)),2)
        status,ticket=self.req('/api/ticket/'+cards[1]['key']);self.assertEqual(status,200)
        self.assertEqual(ticket['owner'],'Otro')

    def test_history_demo_research_summary_report_and_safe_paths(self):
        self.init();status,data=self.req('/api/history?q=DEMO')
        self.assertEqual(status,200);self.assertEqual(len(data['records']),1)
        row=data['records'][0];self.assertEqual(row['time']['date'],'2026-10-24');self.assertIsNone(row['time']['epoch'])
        route='/api/delivery/'+row['home']+'/demo-research'
        status,delivery=self.req(route);self.assertEqual(status,200)
        self.assertIn('opciones para el viaje',delivery['summary'])
        self.assertNotIn('FIN_DEL_INFORME',delivery['summary']);self.assertIn('FIN_DEL_INFORME',delivery['report'])
        self.assertEqual(bridge.human_title('Viaje a Demo research data/demo-research/report.md'),'Viaje a Demo research')
        secret=self.root/'secret';secret.write_text('DO_NOT_DISCLOSE')
        report=Path(self.home.path)/'data/demo-research/report.md';report.unlink();report.symlink_to(secret)
        _,delivery=self.req(route);self.assertNotIn('DO_NOT_DISCLOSE',json.dumps(delivery))
        for path in ['/api/delivery/../demo-research','/%2e%2e/README.md','/icons/../../LICENSE','/api/delivery/'+row['home']+'/%2e%2e']:
            self.assertIn(self.req(path)[0],[400,404])

    def test_explanation_reads_are_inert_and_post_has_stable_owner_receipt(self):
        self.init();identity=bridge.home_id(self.home);route=f'/api/delivery/{identity}/demo-research/explanation'
        _,data=self.req(route);self.assertEqual(data['state'],'absent')
        self.assertFalse((Path(self.home.path)/'explain-demo-research').exists())
        self.assertEqual(self.req(route,{})[1]['state'],'queued')
        self.assertEqual(self.req(route,{})[1]['state'],'queued')
        (Path(self.home.path)/'explain-demo-research').write_text('acknowledged')
        self.assertEqual(self.req(route)[1]['state'],'acknowledged')

    def test_invalid_action_body_and_symlink_assets(self):
        key,body=self.call();route='/api/ticket/'+key+'/answer'
        self.assertEqual(self.req(route,dict(body,note='é'*257))[0],400)
        self.assertEqual(self.req(route,dict(body,value='invented'))[0],409)
        self.assertEqual(self.req(route,dict(body,note='line1\nline2'))[0],400)
        self.assertEqual(self.req(route,dict(body,request_id='short'))[0],400)
        self.assertFalse((Path(self.home.path)/'mutations.jsonl').exists())

    def test_stable_links_survive_restart_and_resolved_call_without_mutation(self):
        board=self.init();identity=bridge.home_id(self.home)
        route=f'/api/resolve?home={identity}&task=ask&view=call'
        status,data=self.req(route);self.assertEqual(status,200);self.assertEqual(data['view'],'call')
        mirror=copy.copy(self.board.collector.snap.cols['captains_call'][0]);mirror.bucket='underway'
        self.board.collector.snap.cols['underway'].append(mirror)
        self.assertEqual(self.req('/api/ticket/'+data['key'])[0],200)
        self.board.collector.snap.cols['underway'].pop()
        restarted=bridge.Board(self.board.data_dir,collector=self.board.collector)
        self.assertEqual(restarted.resolve(identity,'ask','call','local')['key'],data['key'])
        self.board.collector.snap.cols['captains_call']=[]
        status,data=self.req(route);self.assertEqual(status,200);self.assertEqual(data['view'],'unavailable')
        self.assertIn('no longer among current questions',data['message'])
        self.assertFalse((Path(self.home.path)/'mutations.jsonl').exists())
        self.assertEqual(self.req(f'/api/resolve?home={identity}&task=demo-research&view=delivery')[1]['view'],'delivery')
        self.assertEqual(self.req('/api/resolve?home=unknown&task=demo-research&view=delivery')[0],404)

    def test_original_hold_link_never_opens_a_reheld_question_as_the_old_one(self):
        key,body=self.call();identity=bridge.home_id(self.home)
        card=self.board.collector.snap.cols['captains_call'][0]
        route=f'/api/resolve?home={identity}&task=ask&view=call&item=hold:1'
        self.assertEqual(self.req(route)[1]['view'],'call')
        self.assertTrue(self.req('/api/ticket/'+key+'/answer',body)[1]['saved'])
        status,data=self.req(route);self.assertEqual(status,200)
        self.assertEqual(data['view'],'unavailable');self.assertIn('is resolved',data['message'])
        (Path(self.home.path)/'identity').write_text('2026-10-25T17:00:00Z#1')
        (Path(self.home.path)/'open').touch()
        self.board.collector.snap.cols['captains_call']=[card]
        status,data=self.req(route);self.assertEqual(data['view'],'unavailable')
        self.assertIn('earlier question',data['message']);self.assertTrue(data['current_key'])
        self.assertEqual(self.req(route.replace('hold:1','hold:2'))[1]['view'],'call')
        self.assertEqual(len((Path(self.home.path)/'mutations.jsonl').read_text().splitlines()),1)

    def test_owner_cas_race_receipt_and_inherited_namespace_isolation(self):
        key,body=self.call()
        (Path(self.home.path)/'race-rehold').touch()
        with patch.dict(os.environ,FM_DATA_DIR='/unrelated/data',FM_STATE_DIR='/unrelated/state',CODEX_THREAD_ID='not-root'):
            status,receipt=self.req('/api/ticket/'+key+'/answer',body)
        self.assertEqual(status,200);self.assertEqual(receipt['state'],'conflict');self.assertIs(receipt['saved'],False)
        self.assertFalse((Path(self.home.path)/'mutations.jsonl').exists())
        _,data=self.req('/api/receipts');self.assertEqual(len(data['receipts']),1)
        self.assertNotIn('actor',data['receipts'][0])


if __name__=='__main__':unittest.main()
