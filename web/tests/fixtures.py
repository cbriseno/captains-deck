"""Throwaway owner endpoints for HTTP and real-browser checks; never production."""
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge

INTAKE = '''import json, os, sys
from pathlib import Path
root=Path(os.environ['FM_HOME']); args=sys.argv[1:]
if args[0] in ('answers','reconcile-requests') and (root/'race-rehold').exists():
 (root/'identity').write_text('2026-10-25T18:00:00Z#1')
identity=(root/'identity').read_text().strip()
if args[0]=='capabilities':
 print(json.dumps({'schema':'fm-captain-hold.capabilities.v1','expected_identity':['answer','answers','reconcile-requests'],'guarded_input':'single-row'}));sys.exit()
if args[0]=='open':
 print(identity);sys.exit(0 if (root/'open').exists() else 1)
if args[0]=='resolved':
 sys.exit(0 if (root/('resolved-'+args[2])).exists() and not (root/'open').exists() else 1)
if args[0]=='binding': print('captured-source=herdr-firstmate-flow');sys.exit()
if args[0]=='bind': sys.exit()
if '--expected-identity' not in args or args[args.index('--expected-identity')+1]!=identity:
 print(json.dumps({'error':'identity-conflict','reason':'mismatch'}),file=sys.stderr);sys.exit(3)
with (root/'mutations.jsonl').open('a') as out:out.write(json.dumps({'args':args,'input':sys.stdin.read()})+'\\n')
(root/('resolved-'+str(int(identity.rsplit('#',1)[1])+1))).touch()
(root/'open').unlink(missing_ok=True)
print('answers: closed=1 skipped=0')
'''
HISTORY = '''import json,os
root=os.environ['FM_HOME']
print(json.dumps({'schema':'fm-delivery-history.v1','home':root,'complete':True,
'retention':{'done_keep':6},'sources':['current','archive'],'records':[
{'id':'demo-research','title':'Demo research: opciones ordenadas para el viaje','state':'done','kind':'scout',
'completion':{'date':'2026-10-24','verb':'reported'},'source':'archive','body':'Viaje a Demo research, opciones y condiciones verificadas'},
{'id':'phone','title':'Atención del teléfono preparada; validación física pendiente','state':'done','kind':'ship',
'completion':{'date':'2026-10-25','verb':'done'},'source':'current'}]}))
'''
EXPLAIN = '''import json,os,sys
from pathlib import Path
r=Path(os.environ['FM_HOME']);a=sys.argv;request=a[a.index('--request-id')+1];task=a[a.index('--task')+1]
p=r/('explain-'+task)
if '--status' not in a and not p.exists():p.write_text('queued')
state=p.read_text() if p.exists() else 'absent'
reply={'body':'Respuesta retenida del fixture: la comprobación física sigue pendiente. <script>window.REPLY_EXECUTED=true</script>','at':'2026-10-25T16:30:00Z'} if state=='answered' else None
print(json.dumps({'ok':True,'request_id':request,'task':task,'home':str(r),'state':state,
'message':'Estado retenido de la petición.','receipt':{'reply':reply}}))
'''


class FakeCollector:
    def __init__(self, snap, raw):
        self.snap = snap
        self._raw = raw
        self.source_errors = {}

    def snapshot(self):
        now=time.time()
        control=Path(self.snap.homes[-1].path).parent/'source-failed'
        for label,(raw,at,path) in list(self._raw.items()):
            if control.exists():
                self.source_errors[label]='La fuente de prueba no respondió.'
            else:
                self._raw[label]=(raw,now,path)
                self.source_errors[label]=''
        if not (Path(self.snap.homes[-1].path)/'open').exists():
            self.snap.cols['captains_call']=[]
        return self.snap

    def start(self):
        pass

    def stop(self):
        pass

    def refresh_now(self):
        pass


def fixture(root):
    root=Path(root); home=root/'home'
    for part in ('bin','state/decision-cards','data/demo-research','data/phone'):
        (home/part).mkdir(parents=True,exist_ok=True)
    for name, code in [('intake',INTAKE),('history',HISTORY),('explain',EXPLAIN)]:
        (home/'bin'/f'{name}.py').write_text(code)
    for name, target in [('fm-captain-hold.sh','intake'),('fm-delivery-history.sh','history'),('fm-delivery-explain.sh','explain')]:
        path=home/'bin'/name
        path.write_text('#!/bin/sh\n# --expected-identity supported by this test owner\nexec python3 "$(dirname "$0")/'+target+'.py" "$@"\n')
        path.chmod(0o755)
    (home/'identity').write_text('2026-10-25T15:00:00Z#0')
    (home/'open').touch()
    (home/'state/decision-cards/ask.json').write_text(json.dumps({'schema':'fm-decision-card.v1','generated':'2026-10-25T15:00:00Z','card':{
        'key':'ask','type':'decision','title':'Elige el alcance de la próxima entrega',
        'about':'La integración está preparada. Falta tu decisión sobre qué se incluye en esta entrega.',
        'decide':'¿Entregamos el tablero ahora y conservamos la validación física del teléfono como pendiente explícito?',
        'options':[{'value':'scope','label':'Entregar el tablero y registrar la validación pendiente','hint':'Mantiene visible la limitación y permite consultar el progreso fuera de casa.'},
                   {'value':'wait','label':'Esperar la validación física antes de entregar','hint':'El resultado permanece en integración hasta comprobarlo en el dispositivo.'}],
        'recommend_value':'scope','allow_freeform':True,'close':'release'}}))
    (home/'data/demo-research/delivery.md').write_text('# Demo research\n\n## Resultado\nLas opciones para el viaje a Demo research están ordenadas por horario y conveniencia.\n\n## Pendiente\nLa elección y la compra corresponden a la persona.\n')
    (home/'data/demo-research/report.md').write_text('# Informe completo\n\nDetalle retenido para comparar las opciones.\n\n'+('Una línea de evidencia útil, con español y 漢字.\n'*35)+'\nFIN_DEL_INFORME\n<script>window.UNTRUSTED_EXECUTED=true</script>\n[Ejecutar](javascript:alert(1))')
    (home/'data/phone/delivery.md').write_text('# Atención del teléfono\n\n## Resultado\nLa instalación local quedó preparada y las pruebas de escritorio pasaron.\n\n## Pendiente\nLa aceptación en el teléfono físico aún no se ha realizado.\n')
    f=bridge.fm
    h=f.Home('Firstmate',str(home),'captain')
    now=time.time()
    supervisor={'state':'coordinating','summary':'Revisando la entrega web y sus dependencias.','source':'supervisor.declaration','observed_at':f.datetime.fromtimestamp(now,f.timezone.utc).isoformat(),'ttl_seconds':300,'freshness':'fresh'}
    payload={'generated':f.datetime.fromtimestamp(now,f.timezone.utc).isoformat(),'supervisor':supervisor,
     'gates':[{'id':'next','title':'Revisar el resultado del viaje y elegir el siguiente paso','filed':'2026-10-24','blocked_by':'-','owner':'(main)'}],
     'in_flight':[
      {'id':'web','name':'Un dashboard cómodo para consultar el progreso desde fuera de casa','state':'working','flow':{'phase':'active','state':'executing','reason':'Preparando la interfaz móvil y comprobando las rutas de lectura.','next_action':'Validar navegación, errores y respuestas con fixtures aislados.'}},
      {'id':'dependent','name':'Validar la conexión desde el teléfono y conservar el contexto completo','state':'paused','blocked_by':'phone-review','flow':{'phase':'dependency','state':'waiting','reason':'La prueba depende de una sesión coordinada en el teléfono físico. '+('El viewport emulado comprueba el diseño, pero no acredita la aceptación física. '*6)+'FIN_DEL_MOTIVO','waiting_for':'Sesión de validación física coordinada.','next_action':'Firstmate coordina la sesión y conserva el pendiente.','dependencies':[{'id':'phone-review','title':'Comprobar atención y enlace en el teléfono','owner':'Validación móvil','state':'queued','unresolved':True}]}},
      {'id':'integration','name':'Integrar la versión local del historial sin cambiar la retención','state':'done','flow':{'phase':'integration','state':'ready','reason':'Trabajo informado; falta la integración en main local.','next_action':'Firstmate revisa y aterriza la rama.'}}],
     'decisions_open':[{'id':'ask','title':'Elige el alcance de la próxima entrega','owner':'(main)','reason':'Respuesta humana pendiente','flow':{'phase':'human','state':'held','reason':'Confirmar el alcance de la entrega y su validación pendiente.','next_action':'Responder con una opción o con tus palabras.'}}],
     'landed':[{'id':'demo-research','what':'Demo research: opciones ordenadas para el viaje','owner':'(main)','closed':'2026-10-24'},{'id':'phone','what':'Atención del teléfono preparada; validación física pendiente','owner':'(main)','closed':'2026-10-25'}]}
    snap=f.Snapshot();snap.homes=f.inject_all_crew([h]);snap.home=snap.homes[0];snap.collected_at=now
    snap.cols,snap.totals=f.make_cards(h,payload,{}, {}, {},snap.homes)
    raw={h.label:(payload,now,h.path)}
    board=bridge.Board(root/'web-data','https://terminal.example.test',FakeCollector(snap,raw))
    return board,h
