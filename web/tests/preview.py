#!/usr/bin/env python3
"""Local isolated browser fixture. Usage: preview.py /absolute/task-proof-dir [activity|activity-firstmate]."""
from pathlib import Path
import os
import sys
import threading
from fixtures import fixture, bridge
import activity_fleet
root=Path(sys.argv[1]).resolve();root.mkdir(parents=True,exist_ok=True)
os.environ['HERDR_PLUGIN_STATE_DIR']=str(root/'plugin-state')
mode=sys.argv[2] if len(sys.argv)>2 else ''
identity=root/'home/identity'
existing=(identity.read_text(),(root/'home/open').exists()) if identity.exists() else None
if mode in ('activity','activity-firstmate'):
    # The synthetic busy-looking fleet; activity-firstmate adds the fields a companion Firstmate change will export.
    board,home=activity_fleet.fleet(root,firstmate_fields=mode=='activity-firstmate',language=os.environ.get('CD_WEB_LANGUAGE','en'))
else:
    board,home=fixture(root)
    if existing:
        identity.write_text(existing[0])
        if not existing[1]:(root/'home/open').unlink(missing_ok=True)
port=8788
server=bridge.Server(('127.0.0.1',port),board,bridge.Security(port))
print(f'Fixture at http://127.0.0.1:{port}, owner {home.path}',flush=True)
server.serve_forever()
