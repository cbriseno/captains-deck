"""Card account labels: which subscription store runs a ticket."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOW = ROOT / "scripts" / "flow_tui.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_flow():
    spec = importlib.util.spec_from_file_location("flow_tui", FLOW)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["flow_tui"] = mod
    spec.loader.exec_module(mod)
    return mod


flow = load_flow()

ACCOUNTS = {
    "accounts": [
        {"name": "claude-b", "claude_config_dir": "/home/me/.claude-karolina"},
        {"name": "codex-a", "codex_home": "/home/me/.codex-lets-padel"},
    ]
}


def write_accounts(tmp: str, data) -> Path:
    cfg = Path(tmp) / "config"
    cfg.mkdir()
    path = cfg / "crew-accounts.json"
    path.write_text(json.dumps(data))
    return path


def map_for(homes) -> dict[str, str]:
    """The store map one home contributes."""
    return flow.load_account_names(homes)[homes[0].path]


def make_home(path: Path, meta: dict, accounts=None, task: str = "build"):
    (path / "state").mkdir(parents=True)
    (path / "state" / f"{task}.meta").write_text(
        "".join(f"{key}={value}\n" for key, value in meta.items())
    )
    if accounts is not None:
        write_accounts(str(path), accounts)
    return flow.Home(label=path.name, path=str(path), kind="captain")


def test_named_account_wins():
    label = flow.account_label({"harness": "claude", "account": "claude-b"}, {})
    assert label == "claude-b"


def test_vendor_pin_names_the_account_when_account_is_a_store():
    meta = {
        "harness": "claude",
        "account": "/home/me/.claude",
        "account_claude": "claude-a",
    }
    assert flow.account_label(meta, {}) == "claude-a"


def test_pi_provider_becomes_the_label():
    meta = {
        "harness": "pi",
        "account": "/home/me/.pi/agent",
        "account_provider": "deepseek",
        "model": "deepseek/deepseek-flash",
    }
    assert flow.account_label(meta, {}) == "deepseek"


def test_pi_worker_ignores_the_vendor_store_block():
    with tempfile.TemporaryDirectory() as tmp:
        write_accounts(tmp, ACCOUNTS)
        home = flow.Home(label="home", path=tmp, kind="captain")
        names = map_for([home])
        meta = {
            "harness": "pi",
            "account": "/home/me/.pi/agent",
            "account_provider": "deepseek",
            "model": "deepseek/deepseek-flash",
            "account_claude": "claude-a",
            "claude_config_dir": "/home/me/.claude-karolina",
            "codex_home": "/home/me/.codex-lets-padel",
        }
        card = flow.Card()
        card.agent = "pi"
        card.account = flow.account_label(meta, names)
        card.model = meta["model"]
        card.effort = "max"
        assert flow.UI.card_agent_line(card) == "deepseek\u00b7deepseek-flash\u00b7max"


def test_each_vendor_reads_only_its_own_store_field():
    with tempfile.TemporaryDirectory() as tmp:
        write_accounts(tmp, ACCOUNTS)
        home = flow.Home(label="home", path=tmp, kind="captain")
        names = map_for([home])
        claude = {
            "harness": "claude",
            "account": "/home/me/.claude-unknown",
            "codex_home": "/home/me/.codex-lets-padel",
        }
        codex = {
            "harness": "codex",
            "account": "/home/me/.codex-unknown",
            "claude_config_dir": "/home/me/.claude-karolina",
        }
        assert flow.account_label(claude, names) == ""
        assert flow.account_label(codex, names) == ""


def test_store_path_in_account_maps_through_crew_accounts():
    with tempfile.TemporaryDirectory() as tmp:
        write_accounts(tmp, ACCOUNTS)
        home = flow.Home(label="home", path=tmp, kind="captain")
        names = map_for([home])
        claude = {"harness": "claude", "account": "/home/me/.claude-karolina"}
        codex = {"harness": "codex", "account": "/home/me/.codex-lets-padel"}
        assert flow.account_label(claude, names) == "claude-b"
        assert flow.account_label(codex, names) == "codex-a"


def test_vendor_pinned_store_path_maps_through_crew_accounts():
    with tempfile.TemporaryDirectory() as tmp:
        write_accounts(tmp, ACCOUNTS)
        home = flow.Home(label="home", path=tmp, kind="captain")
        names = map_for([home])
        meta = {
            "harness": "claude",
            "account": "ordinary",
            "account_claude": "/home/me/.claude-karolina",
        }
        assert flow.account_label(meta, names) == "claude-b"


def test_unrecorded_store_path_keeps_the_harness_label():
    meta = {"harness": "claude", "account": "/home/me/.claude-unknown"}
    assert flow.account_label(meta, {}) == ""
    assert flow.account_label(meta, {"/home/me/.other": "claude-a"}) == ""


def test_ordinary_account_is_not_a_label():
    assert flow.account_label({"harness": "claude", "account": "ordinary"}, {}) == ""


def test_pi_worker_reads_the_provider_before_the_account_value():
    meta = {
        "harness": "pi",
        "account": "ordinary",
        "account_provider": "deepseek",
        "model": "deepseek/deepseek-flash",
    }
    assert flow.account_label(meta, {}) == "deepseek"


def test_pi_signed_is_a_pi_worker():
    meta = {"harness": "pi-signed", "account": "ordinary", "model": "deepseek/deepseek-flash"}
    assert flow.account_label(meta, {}) == "deepseek"


def test_unknown_record_falls_back_to_the_harness():
    meta = {"harness": "codex", "account": ""}
    assert flow.account_label(meta, {}) == ""


def test_malformed_accounts_shape_contributes_no_names():
    with tempfile.TemporaryDirectory() as tmp:
        write_accounts(tmp, {"accounts": True})
        home = flow.Home(label="home", path=tmp, kind="captain")
        assert flow.load_account_names([home]) == {tmp: {}}


def test_account_names_follow_the_file_on_disk():
    with tempfile.TemporaryDirectory() as tmp:
        entry = {"name": "claude-a", "claude_config_dir": "/home/me/.claude"}
        path = write_accounts(tmp, {"accounts": [entry]})
        home = flow.Home(label="home", path=tmp, kind="captain")
        assert flow.load_account_names([home]) == {tmp: {"/home/me/.claude": "claude-a"}}
        entry["name"] = "claude-b"
        path.write_text(json.dumps({"accounts": [entry]}))
        stat = path.stat()
        os.utime(path, (stat.st_atime + 5, stat.st_mtime + 5))
        assert flow.load_account_names([home]) == {tmp: {"/home/me/.claude": "claude-b"}}


def test_fixture_home_labels_each_ticket_with_its_account():
    home = flow.Home("captain", str(FIXTURES / "home"), "captain")
    snap = json.loads((FIXTURES / "bearings_snapshot.json").read_text())
    cols, _ = flow.make_cards(home, snap, flow.build_meta_index([home]), {}, {}, [home])
    work = {c.task: c for c in cols["underway"]}["work-1"]
    done = {c.task: c for c in cols["underway"]}["done-2"]
    assert flow.UI.card_agent_line(work) == "claude-a\u00b7opus\u00b7xhigh"
    assert flow.UI.card_agent_line(done) == "deepseek\u00b7sonnet\u00b7medium"


def test_same_task_id_in_two_homes_keeps_its_own_account():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        captain = make_home(root / "captain", {"harness": "claude", "account": "claude-a"})
        mate = make_home(root / "mate", {"harness": "codex", "account": "codex-b"})
        for label, home in (("captain work", captain), ("mate work", mate)):
            (Path(home.path) / "state" / "build.status").write_text(f"working: {label}\n")
        homes = [captain, mate]
        meta_index = flow.build_meta_index(homes)
        snap = {"in_flight": [{"id": "build", "name": "Build", "state": "working"}]}
        captain_card = flow.make_cards(captain, snap, meta_index, {}, {}, homes)[0]["underway"][0]
        mate_card = flow.make_cards(mate, snap, meta_index, {}, {}, homes)[0]["underway"][0]
        assert captain_card.account == "claude-a"
        assert mate_card.account == "codex-b"
        assert captain_card.status_text == "captain work"
        assert mate_card.status_text == "mate work"


def test_mirrored_row_resolves_the_owning_home():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        captain = make_home(root / "captain", {"harness": "claude", "account": "claude-a"})
        mate = make_home(root / "mate", {"harness": "codex", "account": "codex-b"})
        mate.kind = "secondmate"
        mate.mate_id = "mate"
        (root / "mate" / "state" / "build.status").write_text("working: mate work\n")
        homes = [captain, mate]
        meta_index = flow.build_meta_index(homes)
        snap = {"in_flight": [{"id": "mate/build", "name": "Build", "state": "working"}]}
        card = flow.make_cards(captain, snap, meta_index, {}, {}, homes)[0]["underway"][0]
        assert (card.task, card.account) == ("build", "codex-b")
        assert card.status_text == "mate work"
        assert card.home_path == captain.path


def test_account_file_belongs_to_the_card_home():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = "/home/me/.claude-shared"
        captain = make_home(
            root / "captain",
            {"harness": "claude", "account": store},
            {"accounts": [{"name": "claude-a", "claude_config_dir": store}]},
        )
        mate = make_home(
            root / "mate",
            {"harness": "claude", "account": store},
            {"accounts": [{"name": "claude-b", "claude_config_dir": store}]},
        )
        homes = [captain, mate]
        meta_index = flow.build_meta_index(homes)
        snap = {"in_flight": [{"id": "build", "name": "Build", "state": "working"}]}
        captain_card = flow.make_cards(captain, snap, meta_index, {}, {}, homes)[0]["underway"][0]
        mate_card = flow.make_cards(mate, snap, meta_index, {}, {}, homes)[0]["underway"][0]
        assert captain_card.account == "claude-a"
        assert mate_card.account == "claude-b"


def test_card_line_shows_the_account_not_the_harness():
    card = flow.Card()
    card.agent = "claude"
    card.account = "claude-a"
    card.model = "claude-opus-5-5"
    card.effort = "xhigh"
    assert flow.UI.card_agent_line(card) == "claude-a\u00b7claude-opus-5-5\u00b7xhigh"


def test_card_line_drops_the_repeated_pi_provider_prefix():
    card = flow.Card()
    card.agent = "pi"
    card.account = "deepseek"
    card.model = "deepseek/deepseek-flash"
    card.effort = "max"
    assert flow.UI.card_agent_line(card) == "deepseek\u00b7deepseek-flash\u00b7max"


def test_card_line_keeps_the_harness_when_no_account_is_known():
    card = flow.Card()
    card.agent = "codex"
    card.model = "gpt-6.1-sol"
    card.effort = ""
    assert flow.UI.card_agent_line(card) == "codex\u00b7gpt-6.1-sol"
