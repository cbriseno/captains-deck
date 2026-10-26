#!/usr/bin/env python3
"""Local isolated browser fixture. Usage: preview.py /absolute/task-proof-dir."""
from pathlib import Path
import os
import sys
import threading
from fixtures import fixture, bridge
root=Path(sys.argv[1]).resolve();root.mkdir(parents=True,exist_ok=True)
os.environ['HERDR_PLUGIN_STATE_DIR']=str(root/'plugin-state')
identity=root/'home/identity'
existing=(identity.read_text(),(root/'home/open').exists()) if identity.exists() else None
board,home=fixture(root)
if existing:
    identity.write_text(existing[0])
    if not existing[1]:(root/'home/open').unlink(missing_ok=True)
port=8788
server=bridge.Server(('127.0.0.1',port),board,bridge.Security(port))
print(f'Fixture at http://127.0.0.1:{port}, owner {home.path}',flush=True)
server.serve_forever()
