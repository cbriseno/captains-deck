"""Remote second mates: routes, the fm-on transport, and the offline state."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FLOW = ROOT / "scripts" / "flow_tui.py"


def load_flow():
    spec = importlib.util.spec_from_file_location("flow_tui", FLOW)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["flow_tui"] = mod
    spec.loader.exec_module(mod)
    return mod


flow = load_flow()
Home = flow.Home
ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

LOCAL = "- pz - Owns the padel app. (home: /srv/pz; scope: the app; projects: pz; added 2026-01-01)\n"
REMOTE = "- vps - Owns the ops domain. (host: vps; root: /srv/fm; home: /srv/fm/home; scope: ops; projects: ops; added 2026-01-02)\n"


@contextlib.contextmanager
def _patched(name: str, value):
    old = getattr(flow, name)
    setattr(flow, name, value)
    try:
        yield
    finally:
        setattr(flow, name, old)


def _captain_home(tmp: str, registry: str) -> str:
    home = Path(tmp) / "firstmate"
    (home / "data" / "task-one").mkdir(parents=True)
    (home / "state").mkdir()
    (home / "bin").mkdir()
    (home / "data" / "captain.md").write_text("# captain\n")
    (home / "data" / "secondmates.md").write_text(registry)
    (home / "bin" / "fm-bearings-snapshot.sh").write_text("#!/bin/sh\nexit 0\n")
    return str(home)


# ---- routes ----------------------------------------------------------------


def test_remote_routes_reads_only_host_root_home_records():
    with tempfile.TemporaryDirectory() as tmp:
        home = _captain_home(tmp, LOCAL + REMOTE)
        assert flow.remote_routes(home) == [("vps", "/srv/fm/home")]


def test_remote_routes_tolerates_a_missing_registry():
    with tempfile.TemporaryDirectory() as tmp:
        assert flow.remote_routes(tmp) == []


def test_discover_homes_adds_a_remote_route_as_a_crew_tab():
    with tempfile.TemporaryDirectory() as tmp:
        home = _captain_home(tmp, LOCAL + REMOTE)
        env = {"FM_FLOW_HOMES": f"captain={home}", "FM_FLOW_HOMES_ONLY": "1"}
        with _patched("_live_agent_homes", lambda: set()), mock.patch.dict(os.environ, env):
            homes = flow.discover_homes()
    labels = [h.label for h in homes]
    assert "captain" in labels and "vps" in labels
    remote = next(h for h in homes if h.label == "vps")
    assert remote.route == "vps" and remote.via == home
    assert remote.path == "/srv/fm/home"
    assert not flow.is_remote_home(next(h for h in homes if h.label == "captain"))


def test_a_local_home_with_the_route_label_wins():
    with tempfile.TemporaryDirectory() as tmp:
        home = _captain_home(tmp, f"- vps - Local one. (home: {tmp}/vps; scope: x; projects: x; added 2026-01-01)\n" + REMOTE)
        env = {"FM_FLOW_HOMES": f"vps={home}", "FM_FLOW_HOMES_ONLY": "1"}
        with _patched("_live_agent_homes", lambda: set()), mock.patch.dict(os.environ, env):
            homes = flow.discover_homes()
    assert [h.label for h in homes].count("vps") == 1
    assert not any(flow.is_remote_home(h) for h in homes)


# ---- the fm-on transport ----------------------------------------------------


def test_bearings_snapshot_routes_a_remote_home_through_fm_on():
    seen: list[str] = []
    with _patched("remote_bearings_snapshot", lambda home: seen.append(home.label) or {"ok": 1}):
        snap = flow.bearings_snapshot(Home("vps", "/srv/fm/home", "secondmate", route="vps", via="/tmp/x"))
    assert snap == {"ok": 1} and seen == ["vps"]


def test_remote_snapshot_uses_fm_on_from_the_captain_home():
    with tempfile.TemporaryDirectory() as tmp:
        home = _captain_home(tmp, REMOTE)
        binp = Path(home) / "bin" / "fm-on.sh"
        argv_file = Path(tmp) / "argv"
        payload = json.dumps({"in_flight": []})
        binp.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$@" > "{argv_file}"\n'
            f"printf '%s' '{payload}'\n"
        )
        binp.chmod(0o755)
        remote = Home("vps", "/srv/fm/home", "secondmate", route="vps", via=home)
        snap = flow.remote_bearings_snapshot(remote)
        argv = argv_file.read_text().splitlines()
    assert snap == {"in_flight": []}
    assert argv == ["vps", "fm-bearings-snapshot.sh", "--json", "--fields", "paths"]


def test_remote_snapshot_without_the_transport_is_empty():
    with tempfile.TemporaryDirectory() as tmp:
        remote = Home("vps", "/srv/fm/home", "secondmate", route="vps", via=tmp)
        assert flow.remote_bearings_snapshot(remote) == {}


# ---- the collector keeps the last good snapshot ------------------------------


EMPTY_SNAP = {"gates": [], "in_flight": [], "decisions_open": [], "landed": []}


def test_a_failed_remote_fetch_keeps_the_last_snapshot_and_marks_it_offline():
    collector = flow.Collector()
    remote = Home("vps", "/srv/fm/home", "secondmate", route="vps", via="/tmp/x")
    collector._raw[remote.label] = (EMPTY_SNAP, 100.0, remote.path)
    with _patched("bearings_snapshot", lambda home: {}):
        raw, _cols, _totals, _loading, err = collector._load_bearings(
            remote, 200.0, {}, {}, [remote]
        )
    assert raw == EMPTY_SNAP and err == ""
    assert remote.label in collector._remote_offline


def test_a_good_remote_fetch_clears_the_offline_mark():
    collector = flow.Collector()
    remote = Home("vps", "/srv/fm/home", "secondmate", route="vps", via="/tmp/x")
    collector._remote_offline[remote.label] = 1.0
    with _patched("bearings_snapshot", lambda home: EMPTY_SNAP):
        collector._load_bearings(remote, 200.0, {}, {}, [remote])
    assert remote.label not in collector._remote_offline


def test_the_snapshot_carries_the_last_seen_epoch():
    collector = flow.Collector()
    remote = Home("vps", "/srv/fm/home", "secondmate", route="vps", via="/tmp/x")
    captain = Home("captain", "/fm", "captain")
    collector._raw[remote.label] = (EMPTY_SNAP, 123.0, remote.path)
    collector._remote_offline[remote.label] = time.time()
    snap = collector._snapshot([captain, remote], remote, {k: [] for k, _ in flow.COLUMNS}, {}, {})
    assert snap.offline == {remote.label: 123.0}


# ---- the board --------------------------------------------------------------


def _render(home: Home, homes: list[Home], offline: dict[str, float]) -> str:
    snap = flow.Snapshot()
    snap.homes = homes
    snap.home = home
    snap.cols = {k: [] for k, _ in flow.COLUMNS}
    snap.offline = dict(offline)
    ui = flow.UI()
    with contextlib.redirect_stdout(io.StringIO()):
        ui.render(snap)
    return "\n".join(ANSI.sub("", line) for line in ui.last_frame or [])


def test_an_offline_remote_tab_shows_offline():
    captain = Home("captain", "/fm", "captain")
    remote = Home("vps", "/srv/fm/home", "secondmate", route="vps", via="/tmp/x")
    text = _render(captain, [captain, remote], {remote.label: time.time() - 300})
    assert "vps" in text and "offline" in text


def test_the_active_remote_tab_names_when_it_was_last_seen():
    remote = Home("vps", "/srv/fm/home", "secondmate", route="vps", via="/tmp/x")
    text = _render(remote, [remote], {remote.label: time.time() - 300})
    assert "offline, last seen" in text


def test_a_remote_captains_call_is_read_only():
    remote = Home("vps", "/srv/fm/home", "secondmate", route="vps", via="/tmp/x")
    ui = flow.UI()
    snap = flow.Snapshot()
    snap.homes = [remote]
    snap.home = remote
    ui.collector._publish(snap)
    card = flow.Card()
    card.bucket = "captains_call"
    card.id = card.task = "t-1"
    ui.open_decision(card)
    assert ui.dialog is None
    assert "read-only" in ui.flash


def test_readme_documents_remote_secondmates():
    readme = (ROOT / "README.md").read_text()
    assert "Remote second mates" in readme
    assert "fm-on.sh" in readme


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ok")
