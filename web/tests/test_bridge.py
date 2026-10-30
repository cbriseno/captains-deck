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


def delegated_fixture(root):
    f=bridge.fm
    board,parent=fixture(root/'parent')
    second,mate=fixture(root/'mate')
    mate.label='2ndmate-lane';mate.mate_id='lane';mate.kind='secondmate'
    (Path(mate.path)/'state/decision-cards/ask.json').unlink()
    path=Path(parent.path)/'state/decision-cards/ask.json'
    data=json.loads(path.read_text());path.unlink();data['card']['key']='docs'
    (path.parent/'docs.json').write_text(json.dumps(data))
    parent_raw=board.collector._raw[parent.label][0]
    parent_raw['decisions_open'][0].update(id='docs',owner='lane')
    mate_raw=second.collector._raw['Firstmate'][0]
    mate_raw['decisions_open'][0]['id']='docs'
    snap=board.collector.snap;snap.homes.append(mate)
    received=f.time.time()
    snap.bearings={f.delivery_home_identity(parent):(parent_raw,received),
                   f.delivery_home_identity(mate):(mate_raw,received)}
    parts=[]
    for home,raw in [(parent,parent_raw),(mate,mate_raw)]:
        cols,totals=f.make_cards(home,raw,{}, {}, {},snap.homes,bearings=snap.bearings)
        parts.append((home,cols,totals))
    snap.cols,snap.totals=f.merge_fleet_columns(parts)
    return board,parent,mate,board.key(snap.cols['captains_call'][0]),received


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
        self.assertEqual([c['id'] for c in board['columns']],['captains_call','underway','charted','landed'])
        self.assertEqual(len(board['columns'][1]['cards']),3)
        self.assertEqual(board['columns'][1]['cards'][2]['context']['phase'],'integration')
        self.assertFalse((Path(self.home.path)/'mutations.jsonl').exists())
        self.assertTrue(all('home_path' not in c for col in board['columns'] for c in col['cards']))

    def test_call_report_link_opens_a_bounded_local_file_only(self):
        board=self.init();key=next(c for c in board['columns'] if c['id']=='captains_call')['cards'][0]['key']
        card=self.board.collector.snap.cols['captains_call'][0]
        def link(value):
            card.call={'question':'¿Seguimos?','options':[{'id':'yes','label':'Sí','detail':''}],'recommended':'yes','context':'','link':value}
            return self.req('/api/ticket/'+key+'/report')
        status,data=link('data/demo-research/report.md')
        self.assertEqual(status,200);self.assertEqual(data['path'],'data/demo-research/report.md')
        self.assertIn('FIN_DEL_INFORME',data['text'])
        status,data=link(str(Path(self.home.path)/'data/demo-research/report.md'))
        self.assertEqual(status,200);self.assertEqual(data['path'],'data/demo-research/report.md')
        for refused in ['https://example.com/report','state/decision-cards/ask.json','data/demo-research/../../identity',
                        'data/demo-research/report.json','/etc/passwd','data/missing/report.md','']:
            with self.subTest(link=refused):
                status,data=link(refused)
                self.assertEqual(status,404,data);self.assertNotIn('text',data)
        (Path(self.home.path)/'data/demo-research/linked.md').symlink_to(Path(self.home.path)/'identity')
        status,data=link('data/demo-research/linked.md');self.assertEqual(status,404);self.assertNotIn('text',data)
        landed=next(c for c in board['columns'] if c['id']=='underway')['cards'][0]['key']
        self.assertEqual(self.req('/api/ticket/'+landed+'/report')[0],404)
        self.assertEqual(self.req('/api/ticket/'+key+'/report',body={})[0],404)
        self.assertFalse((Path(self.home.path)/'mutations.jsonl').exists())

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
        board=self.init();cards=next(c for c in board['columns'] if c['id']=='captains_call')['cards']
        self.assertEqual(len(set(c['key'] for c in cards)),2)
        status,ticket=self.req('/api/ticket/'+cards[1]['key']);self.assertEqual(status,200)
        self.assertEqual(ticket['owner'],'Otro')

    def test_fleet_merge_keeps_each_homes_same_named_call_and_opens_its_own_question(self):
        second,home2=fixture(self.root/'second')
        home2.label='Otro'
        snap=self.board.collector.snap
        parts=[(self.home,snap.cols,snap.totals),(home2,second.collector.snap.cols,second.collector.snap.totals)]
        for home,cols,_ in parts:
            path=Path(home.path)/'state/decision-cards/ask.json'
            data=json.loads(path.read_text());path.unlink()
            data['generated']=home.label+'-stamp'
            data['card'].update(key='docs',title=home.label+' question',about=home.label+' context',
                                options=[{'value':home.label,'label':home.label+' answer'}])
            (path.parent/'docs.json').write_text(json.dumps(data))
            card=cols['captains_call'][0]
            card.task=card.id='docs';card.title=home.label+' durable question'
        snap.homes.append(home2)
        snap.cols,snap.totals=bridge.fm.merge_fleet_columns(parts)
        self.assertEqual(snap.totals['captains_call'],2)
        cards=next(c for c in self.init()['columns'] if c['id']=='captains_call')['cards']
        self.assertEqual([c['task'] for c in cards],['docs','docs'])
        owners=[]
        tickets=[]
        for card in cards:
            status,ticket=self.req('/api/ticket/'+card['key']);self.assertEqual(status,200)
            self.assertEqual(ticket['home'],card['home']);self.assertFalse(ticket['call']['error'])
            self.assertEqual(ticket['call']['title'],ticket['owner']+' question')
            self.assertEqual(ticket['call']['about'],ticket['owner']+' context')
            self.assertEqual(ticket['call']['options'][0]['value'],ticket['owner'])
            self.assertEqual(ticket['call']['presentation']['about'],ticket['owner']+' context')
            self.assertEqual(ticket['call']['presentation']['options'][0]['value'],ticket['owner'])
            owners.append(ticket['owner'])
            tickets.append(ticket)
        self.assertEqual(owners,['Firstmate','Otro'])
        for card,ticket in zip(cards,tickets):
            body=dict(value=ticket['owner'],note='',occurrence=ticket['call']['occurrence'],
                      request_id=ticket['owner']+'a'*32)
            status,result=self.req('/api/ticket/'+card['key']+'/answer',body)
            self.assertEqual(status,200);self.assertTrue(result['saved'])
        for home in (self.home,home2):
            mutations=(Path(home.path)/'mutations.jsonl').read_text().splitlines()
            self.assertEqual(len(mutations),1)
            self.assertEqual(json.loads(mutations[0])['input'].split('\t')[:2],['docs',home.label])

    def test_ticket_without_own_content_does_not_borrow_another_homes_question(self):
        second,home2=fixture(self.root/'second');home2.label='Otro'
        snap=self.board.collector.snap
        other=second.collector.snap.cols['captains_call'][0]
        other.title='Otro durable question'
        (Path(home2.path)/'state/decision-cards/ask.json').unlink()
        snap.homes.append(home2)
        snap.cols,snap.totals=bridge.fm.merge_fleet_columns(
            [(self.home,snap.cols,snap.totals),(home2,second.collector.snap.cols,second.collector.snap.totals)])
        cards=next(c for c in self.init()['columns'] if c['id']=='captains_call')['cards']
        status,ticket=self.req('/api/ticket/'+cards[1]['key']);self.assertEqual(status,200)
        self.assertEqual(ticket['owner'],'Otro')
        self.assertEqual(ticket['call']['title'],'Otro durable question')
        self.assertEqual([o['value'] for o in ticket['call']['options']],['reconcile'])
        self.assertEqual([o['value'] for o in ticket['call']['presentation']['options']],['reconcile'])
        self.assertFalse(ticket['call']['error'])

    def test_parent_ticket_reuses_published_ownership_without_snapshot_walks(self):
        f=bridge.fm
        second,mate=fixture(self.root/'second')
        mate.label='2ndmate-lane';mate.mate_id='lane';mate.kind='secondmate'
        (Path(mate.path)/'state/decision-cards/ask.json').unlink()
        snap=self.board.collector.snap
        snap.homes.append(mate)
        parent_raw=self.board.collector._raw[self.home.label][0]
        parent_raw['decisions_open'][0]['owner']='lane'
        mate_raw=second.collector._raw['Firstmate'][0]
        snap.bearings={f.delivery_home_identity(self.home):(parent_raw,f.time.time()),
                       f.delivery_home_identity(mate):(mate_raw,f.time.time())}
        with patch.object(f,'bearings_snapshot',side_effect=AssertionError('unexpected snapshot walk')):
            parts=[]
            for home,raw in [(self.home,parent_raw),(mate,mate_raw)]:
                cols,totals=f.make_cards(home,raw,{}, {}, {},snap.homes,bearings=snap.bearings)
                parts.append((home,cols,totals))
            snap.cols,snap.totals=f.merge_fleet_columns(parts)
            cards=next(c for c in self.init()['columns'] if c['id']=='captains_call')['cards']
            self.assertEqual(len(cards),1)
            for _ in range(2):
                status,ticket=self.req('/api/ticket/'+cards[0]['key'])
                self.assertEqual(status,200);self.assertFalse(ticket['call']['error'])
                self.assertEqual(ticket['owner'],mate.label)
                self.assertEqual(ticket['call']['options'][0]['value'],'scope')
                self.assertEqual(ticket['call']['presentation']['options'][0]['value'],'scope')
                self.assertEqual(self.board.content(snap.cols['captains_call'][0],mate)['stamp'],
                                 '2026-10-25T15:00:00Z')
            received=snap.bearings[f.delivery_home_identity(self.home)][1]
            with patch.object(f,'_bearings_ttl',return_value=60.0), \
                    patch.object(f.time,'time',return_value=received+61.0):
                status,ticket=self.req('/api/ticket/'+cards[0]['key'])
                self.assertEqual(status,200);self.assertFalse(ticket['call']['error'])
                self.assertEqual([o['value'] for o in ticket['call']['options']],['reconcile'])
                self.assertEqual([o['value'] for o in ticket['call']['presentation']['options']],['reconcile'])

    def test_submit_rejects_changed_content_during_question_checks(self):
        f=bridge.fm
        for change in ('expire','reorder'):
            for value in ('scope','wait'):
                with self.subTest(change=change,value=value):
                    board,parent,mate,key,received=delegated_fixture(self.root/change/value)
                    with patch.object(f,'_bearings_ttl',return_value=60.0), \
                            patch.object(f.time,'time',return_value=received+59.0) as clock, \
                            patch.object(f,'bearings_snapshot',side_effect=AssertionError('unexpected snapshot walk')):
                        ticket=board.ticket(key)
                        body=dict(value=value,note='',occurrence=ticket['call']['occurrence'],request_id='s'*32)
                        guard=board.guarded_intake

                        def change_during_guard(home):
                            result=guard(home)
                            if change=='expire':
                                clock.return_value=received+61.0
                            else:
                                path=Path(parent.path)/'state/decision-cards/docs.json'
                                data=json.loads(path.read_text());data['card']['options'].reverse()
                                path.write_text(json.dumps(data))
                            return result

                        with patch.object(board,'guarded_intake',side_effect=change_during_guard):
                            with self.assertRaisesRegex(bridge.Refusal,'The question changed') as refusal:
                                board.submit(key,body,'local')
                        self.assertEqual(refusal.exception.status,409)
                        self.assertFalse((Path(mate.path)/'mutations.jsonl').exists())
                        self.assertFalse((board.data_dir/'answers').exists())
                        snap=board.collector.snap
                        snap.bearings={identity:(raw,clock.return_value) for identity,(raw,_) in snap.bearings.items()}
                        body['occurrence']=board.ticket(key)['call']['occurrence']
                        self.assertTrue(board.submit(key,body,'local')['saved'])
                        mutations=(Path(mate.path)/'mutations.jsonl').read_text().splitlines()
                        self.assertEqual(len(mutations),1)
                        mutation=json.loads(mutations[0])
                        self.assertEqual(mutation['args'][0],'answers')
                        self.assertEqual(mutation['input'].split('\t')[:2],['docs',value])
                        self.assertFalse((Path(parent.path)/'mutations.jsonl').exists())

    def test_ticket_presentation_uses_the_same_question_when_evidence_expires(self):
        f=bridge.fm
        board,parent,mate,key,received=delegated_fixture(self.root/'presentation-expiry')
        board.i18n=bridge.Localization('es');board.t=board.i18n.t
        with patch.object(f,'_bearings_ttl',return_value=60.0), \
                patch.object(f.time,'time',return_value=received+59.0) as clock, \
                patch.object(f,'bearings_snapshot',side_effect=AssertionError('unexpected snapshot walk')):
            guard=board.guarded_intake

            def expire_during_guard(home):
                result=guard(home)
                clock.return_value=received+61.0
                return result

            with patch.object(board,'guarded_intake',side_effect=expire_during_guard):
                call=board.ticket(key)['call']
            self.assertEqual([o['value'] for o in call['options']],['scope','wait','reconcile'])
            self.assertEqual([o['value'] for o in call['presentation']['options']],['scope','wait','reconcile'])
            self.assertEqual(call['presentation']['options'][:-1],call['options'][:-1])
            self.assertEqual(call['presentation']['options'][-1]['label'],'Revisar otra vez primero')
            expired=board.ticket(key)['call']
            self.assertEqual([o['value'] for o in expired['options']],['reconcile'])
            self.assertEqual([o['value'] for o in expired['presentation']['options']],['reconcile'])
            self.assertFalse((Path(mate.path)/'mutations.jsonl').exists())

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

    def test_long_original_delivery_and_report_stay_complete_through_existing_routes(self):
        Path(self.home.path,'data/demo-research/delivery.md').write_text('# Demo research\n\n## Resultado\n'+'Opción de viaje con horario y evidencia. '*30+'FIN_DEL_RESULTADO\n\n## Pendiente\nLa compra corresponde a la persona.\n')
        self.init();_,data=self.req('/api/history?q=DEMO');row=data['records'][0]
        status,delivery=self.req('/api/delivery/'+row['home']+'/demo-research');self.assertEqual(status,200)
        self.assertEqual(delivery['summary'],'Resultado\n'+'Opción de viaje con horario y evidencia. '*30+'FIN_DEL_RESULTADO\nPendiente\nLa compra corresponde a la persona.')
        self.assertEqual(delivery['report'].count('Una línea de evidencia útil'),35)
        self.assertIn('FIN_DEL_INFORME',delivery['report'])

    def test_history_is_newest_first_by_the_time_shown_and_titles_carry_no_urls(self):
        records=[{'id':'untimed','title':'Sin hora'},
                 {'id':'old-day','title':'Día viejo','completion':{'date':'2026-10-10'}},
                 {'id':'noon','title':'Mediodía','completion':{'value':'2026-10-20T12:00:00Z'}},
                 {'id':'same-day','title':'Solo el día','completion':{'date':'2026-10-20'}},
                 {'id':'evening','title':'Tablero listo https://github.com/example/deck/pull/8','completion':{'value':'2026-10-20T15:30:00Z'}},
                 {'id':'newer','title':'Arreglo (PR https://github.com/example/deck/pull/9).','completion':{'date':'2026-10-21'}},
                 # 20:00 in Tokyo is 11:00 UTC: older than noon, though its text sorts after it.
                 {'id':'tokyo','title':'Desde Tokio','completion':{'value':'2026-10-20T20:00:00+09:00'}},
                 # The producer's delivered time is what the row shows, not the closing date.
                 {'id':'producer','title':'Con hora del productor','completion':{'date':'2026-10-09'},
                  'card_time':{'kind':'delivered','precision':'second','source':'completion.json','value':'2026-10-22T08:00:00Z'}}]
        (Path(self.home.path)/'bin/history.py').write_text('import json,os\nprint(json.dumps({"schema":"fm-delivery-history.v1","home":os.environ["FM_HOME"],'
            '"complete":True,"records":'+json.dumps(records)+'}))\n')
        self.init();_,data=self.req('/api/history')
        rows=data['records']
        # The raw closing text would put the date-only rows before timed ones.
        self.assertEqual([r['task'] for r in rows],['producer','newer','evening','noon','tokyo','same-day','old-day','untimed'])
        self.assertEqual([r['title'] for r in rows][1:3],['Arreglo.','Tablero listo'])
        self.assertFalse(any('://' in r['title'] for r in rows))
        _,delivery=self.req('/api/delivery/'+rows[2]['home']+'/evening')
        self.assertEqual(delivery['title'],'Tablero listo')
        self.assertNotIn('://',delivery['summary'])
        self.assertEqual(delivery['links'],[{'url':'https://github.com/example/deck/pull/8','pr':8}])
        _,plain=self.req('/api/delivery/'+rows[0]['home']+'/old-day')
        self.assertEqual(plain['links'],[])

    def test_titles_drop_web_addresses_and_keep_the_rest(self):
        for raw,title in [('Mejor aviso https://github.com/a/b/pull/8','Mejor aviso'),
                          ('Hecho: https://example.com/x, listo','Hecho, listo'),
                          ('[PR #3](https://github.com/a/b/pull/3) Tablero','PR #3 Tablero'),
                          ('Texto: sin enlace ; igual','Texto: sin enlace ; igual')]:
            self.assertEqual(bridge.human_title(raw),title)
        self.assertEqual(bridge.title_links('a https://github.com/a/b/pull/12. y https://example.com/z?q=1 y https://github.com/a/b/pull/12'),
                         [{'url':'https://github.com/a/b/pull/12','pr':12},{'url':'https://example.com/z?q=1','pr':None}])

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
        self.assertIn('isn’t waiting anymore',data['message'])
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
        self.assertEqual(data['view'],'unavailable');self.assertIn('already closed',data['message'])
        (Path(self.home.path)/'identity').write_text('2026-10-25T17:00:00Z#1')
        (Path(self.home.path)/'open').touch()
        self.board.collector.snap.cols['captains_call']=[card]
        status,data=self.req(route);self.assertEqual(data['view'],'unavailable')
        self.assertIn('earlier version of the question',data['message']);self.assertTrue(data['current_key'])
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

    def test_intake_environment_drops_codex_and_claude_supervisor_identity(self):
        names=('CODEX_THREAD_ID','CODEX_SESSION_ID','CLAUDE_SESSION_ID','CLAUDE_CODE_SESSION_ID','CLAUDE_PID',
               'FM_TASK_ID','HERDR_PANE_ID')
        with patch.dict(os.environ,{name:'supervisor' for name in names}):
            env=bridge.Board.child_env(self.home)
        self.assertEqual(set(names)&set(env),set());self.assertEqual(env['FM_HOME'],self.home.path)

    def herdr(self,refuse=False):
        """A stand-in for `herdr agent prompt` that records every send."""
        log=self.root/'herdr-prompts.jsonl';script=self.root/'herdr'
        script.write_text('#!/bin/sh\n'+(
            'echo \'{"error":{"code":"agent_blocked","message":"busy"}}\' >&2; exit 1\n' if refuse else
            'python3 -c \'import json,sys;open(sys.argv[1],"a").write(json.dumps(sys.argv[2:])+"\\n")\' "'+str(log)+'" "$@"\n'))
        script.chmod(0o755)
        self.enterContext(patch.object(bridge.fm,'HERDR_BIN',str(script)))
        return log

    def sends(self,log):
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def primary(self):
        self.board.collector.snap.agents={'w-9':{'cwd':str(self.root/'elsewhere')},'p-1':{'cwd':self.home.path}}

    def test_main_owned_answer_points_the_idle_primary_at_the_record_with_split_root(self):
        code=self.root/'firstmate code';code.mkdir()
        (Path(self.home.path)/'bin').rename(code/'bin')
        self.enterContext(patch.dict(os.environ,FM_ROOT_OVERRIDE=str(code)))
        log=self.herdr();self.primary();key,body=self.call()
        status,receipt=self.req('/api/ticket/'+key+'/answer',body)
        self.assertEqual(status,200);self.assertIs(receipt['saved'],True);self.assertEqual(receipt['primary'],'sent')
        self.assertEqual(len((Path(self.home.path)/'mutations.jsonl').read_text().splitlines()),1)
        sent=self.sends(log);self.assertEqual(len(sent),1)
        self.assertEqual(sent[0][:3],['agent','prompt','p-1'])
        self.assertEqual(sent[0][3],"Captain's Deck: the captain answered ask from the web board. "
                                  "Read the recorded answer for this task.")
        self.assertNotIn(body['note'],sent[0][3]);self.assertNotIn('scope',sent[0][3])
        _,stored=self.req('/api/receipts');self.assertEqual(stored['receipts'][0]['primary'],'sent')

    def test_existing_answer_receipt_skips_wake_on_retry_and_restart(self):
        log=self.herdr();self.primary();key,body=self.call()
        with patch.object(self.board,'wake_primary',wraps=self.board.wake_primary) as wake:
            with ThreadPoolExecutor(2) as pool:
                replies=list(pool.map(lambda _:self.board.submit(key,body,'local'),range(2)))
            self.assertTrue(all(reply['saved'] for reply in replies))
            self.assertEqual(sum(bool(reply.get('replay')) for reply in replies),1)
            self.assertEqual(wake.call_count,1)
            wake.reset_mock()
            self.assertTrue(self.board.submit(key,body,'local')['replay'])
            self.assertTrue(self.board.submit(key,dict(body,request_id='b'*32),'local')['replay'])
            wake.assert_not_called()
        restarted=bridge.Board(self.board.data_dir,collector=self.board.collector)
        with patch.object(restarted,'wake_primary',wraps=restarted.wake_primary) as wake:
            self.assertTrue(restarted.submit(key,body,'local')['replay'])
            self.assertTrue(restarted.submit(key,dict(body,request_id='c'*32),'local')['replay'])
            wake.assert_not_called()
        self.assertEqual(len(self.sends(log)),1)
        self.assertEqual(len((Path(self.home.path)/'mutations.jsonl').read_text().splitlines()),1)

    def test_unreachable_primary_keeps_the_answer_and_says_when_firstmate_sees_it(self):
        log=self.herdr();key,body=self.call()
        status,receipt=self.req('/api/ticket/'+key+'/answer',body)
        self.assertEqual(status,200);self.assertIs(receipt['saved'],True);self.assertEqual(receipt['primary'],'unreachable')
        self.assertEqual(self.sends(log),[])
        self.assertEqual(len((Path(self.home.path)/'mutations.jsonl').read_text().splitlines()),1)
        self.herdr(refuse=True);self.primary()
        stub=type('Card',(),{'owner':'(main)','task':'ask'})()
        self.assertEqual(self.board.wake_primary(self.home,stub),'unreachable')

    def test_conflicted_answer_and_reconcile_never_message_the_primary(self):
        log=self.herdr();self.primary();key,body=self.call()
        (Path(self.home.path)/'race-rehold').touch()
        self.assertEqual(self.req('/api/ticket/'+key+'/answer',body)[1]['state'],'conflict')
        (Path(self.home.path)/'race-rehold').unlink()
        key,body=self.call()
        status,receipt=self.req('/api/ticket/'+key+'/answer',dict(body,value='reconcile',note='',request_id='r'*32))
        self.assertEqual(status,200);self.assertIs(receipt['saved'],True);self.assertNotIn('primary',receipt)
        self.assertEqual(self.sends(log),[])

    def test_worker_owned_decision_never_messages_the_primary(self):
        log=self.herdr();self.primary()
        self.home.mate_id='mate-a'
        self.board.collector.snap.cols['captains_call'][0].owner='mate-a'
        key,body=self.call()
        status,receipt=self.req('/api/ticket/'+key+'/answer',body)
        self.assertEqual(status,200);self.assertIs(receipt['saved'],True);self.assertNotIn('primary',receipt)
        self.assertEqual(self.sends(log),[])

    def test_structured_call_options_reach_the_ticket_and_an_option_id_answers(self):
        # A row with a structured call and no composed card: the options come
        # from the row, never from the prose reason beside it.
        (Path(self.home.path)/'state/decision-cards/ask.json').unlink()
        card=self.board.collector.snap.cols['captains_call'][0]
        card.call=bridge.fm.structured_call({'question':'¿Entregamos ahora?','context':'El tablero está listo.',
            'options':[{'id':'ship','label':'Entregar ahora','detail':'La validación queda pendiente'},{'id':'wait','label':'Esperar'}],
            'recommended':'ship','link':'https://example.com/informe'})
        board=self.init()
        listed=next(c for c in board['columns'] if c['id']=='captains_call')['cards'][0]
        self.assertEqual(listed['question'],'¿Entregamos ahora?')
        status,ticket=self.req('/api/ticket/'+listed['key']);self.assertEqual(status,200)
        call=ticket['call']
        self.assertEqual([o['value'] for o in call['options']],['ship','wait','reconcile'])
        self.assertEqual((call['recommend'],call['decide'],call['about'],call['link']),
                         ('ship','¿Entregamos ahora?','El tablero está listo.','https://example.com/informe'))
        self.assertTrue(call['background'])
        status,_=self.req('/api/ticket/'+listed['key']+'/answer',dict(value='other',note='',occurrence=call['occurrence'],request_id='t'*32))
        self.assertEqual(status,409);self.assertFalse((Path(self.home.path)/'mutations.jsonl').exists())
        status,receipt=self.req('/api/ticket/'+listed['key']+'/answer',dict(value='ship',note='',occurrence=call['occurrence'],request_id='s'*32))
        self.assertEqual(status,200);self.assertIs(receipt['saved'],True)
        mutation=json.loads((Path(self.home.path)/'mutations.jsonl').read_text())
        self.assertEqual(mutation['args'][0],'answers')
        self.assertEqual(mutation['input'].split('\t')[:2],['ask','ship'])

    def markdown(self):
        """Give the fixture home the Markdown backlog Firstmate reads calls from."""
        root=Path(self.home.path)
        (root/'.tasks.toml').write_text('backend = "markdown"\n\n[markdown]\npath = "data/backlog.md"\narchive = "data/done-archive.md"\n')
        (root/'data/backlog.md').write_text('# Backlog\n\n## In flight\n- [ ] other - Otra tarea (kind: ship)\n'
            '  Sin cambios.\n- [ ] ask - Elige el alcance (kind: captain) (hold-kind: captain)\n'
            '  Captain hold set: 2026-10-25T15:00:00Z\n\n## Done\n- [x] demo-research - Demo research (reported 2026-10-24)\n')
        return root

    def calls(self,prefix):
        log=Path(self.home.path)/'calls.log'
        return sum(line.startswith(prefix) for line in log.read_text().splitlines()) if log.exists() else 0

    def edit_row(self,root,task,line):
        path=root/'data/backlog.md';text=path.read_text()
        head=next(l for l in text.splitlines() if l.startswith('- [ ] '+task+' '))
        path.write_text(text.replace(head+'\n',head+'\n  '+line+'\n'))

    def test_opening_a_call_reuses_its_identity_until_its_backlog_row_changes(self):
        root=self.markdown();key=next(c for c in self.init()['columns'] if c['id']=='captains_call')['cards'][0]['key']
        first=[self.req('/api/ticket/'+key)[1]['call']['occurrence'] for _ in range(3)]
        self.assertEqual(len(set(first)),1);self.assertEqual(self.calls('open ask --identity'),1)
        with ThreadPoolExecutor(4) as pool:
            self.assertEqual(set(pool.map(lambda _:self.board.ticket(key)['call']['occurrence'],range(4))),set(first))
        self.edit_row(root,'other','Otra nota.')
        self.assertEqual(self.req('/api/ticket/'+key)[1]['call']['occurrence'],first[0])
        self.assertEqual(self.calls('open ask --identity'),1)
        # Re-holding the call rewrites its own row, so the next open asks again.
        (root/'identity').write_text('2026-10-25T16:00:00Z#1');self.edit_row(root,'ask','Resolution recorded by fm-captain-hold.')
        _,ticket=self.req('/api/ticket/'+key)
        self.assertEqual(ticket['call']['identity'],'2026-10-25T16:00:00Z#1');self.assertNotEqual(ticket['call']['occurrence'],first[0])
        self.assertEqual(self.calls('open ask --identity'),2)

    def test_an_answer_checks_the_live_call_and_a_missed_change_does_not_stick(self):
        self.markdown();key,body=self.call()
        # Firstmate moved on without the row changing: the open view is stale,
        # but the answer is checked against the live identity and refused.
        (Path(self.home.path)/'identity').write_text('2026-10-25T16:00:00Z#1')
        self.assertEqual(self.req('/api/ticket/'+key)[1]['call']['occurrence'],body['occurrence'])
        self.assertEqual(self.req('/api/ticket/'+key+'/answer',body)[0],409)
        self.assertFalse((Path(self.home.path)/'mutations.jsonl').exists())
        _,ticket=self.req('/api/ticket/'+key);self.assertEqual(ticket['call']['identity'],'2026-10-25T16:00:00Z#1')
        status,receipt=self.req('/api/ticket/'+key+'/answer',dict(body,occurrence=ticket['call']['occurrence'],request_id='n'*32))
        self.assertEqual(status,200);self.assertIs(receipt['saved'],True)
        mutation=json.loads((Path(self.home.path)/'mutations.jsonl').read_text())
        self.assertEqual(mutation['args'][-1],'2026-10-25T16:00:00Z#1')

    def test_calls_without_a_readable_markdown_row_ask_firstmate_on_every_open(self):
        key=next(c for c in self.init()['columns'] if c['id']=='captains_call')['cards'][0]['key']
        root=Path(self.home.path)
        for setup in (lambda:None,lambda:(root/'.tasks.toml').write_text('backend = "github"\n'),
                      lambda:self.markdown() and (root/'.tasks.toml').write_text('backend = [broken'),
                      lambda:(root/'.tasks.toml').unlink() or (root/'data/backlog.md').open('a').write('- [ ] ask - copia (kind: captain)\n')):
            with self.subTest(setup=setup):
                setup();before=self.calls('open ask --identity')
                for _ in range(2):self.assertEqual(self.req('/api/ticket/'+key)[0],200)
                self.assertEqual(self.calls('open ask --identity'),before+2)

    def test_opening_a_delivery_reads_history_again_only_when_its_sources_change(self):
        root=self.markdown();identity=bridge.home_id(self.home)
        route=f'/api/delivery/{identity}/demo-research'
        for _ in range(3):self.assertEqual(self.req(route)[0],200)
        self.assertEqual(self.calls('history'),1)
        self.edit_row(root,'other','Otra nota.');self.assertEqual(self.req(route)[0],200)
        self.assertEqual(self.calls('history'),2)
        (root/'data/demo-research/completion.json').write_text('{}');self.assertEqual(self.req(route)[0],200)
        self.assertEqual(self.calls('history'),3)
        (root/'.tasks.toml').unlink();self.assertEqual(self.req(route)[0],200)
        self.assertEqual(self.calls('history'),4)
        (root/'.tasks.toml').write_text('backend = "github"\n')
        for _ in range(2):self.assertEqual(self.req(route)[0],200)
        self.assertEqual(self.calls('history'),6)

    def test_read_ahead_warms_open_calls_and_history_once_per_change(self):
        root=self.markdown();key=next(c for c in self.init()['columns'] if c['id']=='captains_call')['cards'][0]['key']
        self.board.prefetch();self.board.prefetch()
        self.assertEqual((self.calls('open ask --identity'),self.calls('history')),(1,1))
        self.assertEqual(self.req('/api/ticket/'+key)[0],200)
        self.assertEqual(self.req(f'/api/delivery/{bridge.home_id(self.home)}/demo-research')[0],200)
        self.assertEqual((self.calls('open ask --identity'),self.calls('history')),(1,1))
        self.board.identities.clear()
        with patch.object(self.board,'open_identity',side_effect=bridge.Refusal('no')) as live:
            self.board.prefetch();self.board.prefetch();self.assertEqual(live.call_count,1)
            self.edit_row(root,'ask','Otra nota.');self.board.prefetch();self.assertEqual(live.call_count,2)
        (root/'open').unlink();self.board.prefetch()
        self.assertEqual((self.board.identities,self.board.warm_failed),({},{}))

    def test_started_board_reads_ahead_in_the_background_and_stops(self):
        self.markdown();self.board.start();self.addCleanup(self.board.stop)
        self.init()
        for _ in range(100):
            if self.calls('open ask --identity') and self.calls('history'):break
            threading.Event().wait(0.05)
        self.assertEqual((self.calls('open ask --identity'),self.calls('history')),(1,1))
        self.board.stop();self.assertTrue(self.board.stopping.is_set())

if __name__=='__main__':unittest.main()
