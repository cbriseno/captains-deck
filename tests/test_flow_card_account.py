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


def test_store_path_in_account_maps_through_crew_accounts():
    with tempfile.TemporaryDirectory() as tmp:
        write_accounts(tmp, ACCOUNTS)
        home = flow.Home(label="home", path=tmp, kind="captain")
        names = flow.load_account_names([home])
        claude = {"harness": "claude", "account": "/home/me/.claude-karolina"}
        codex = {"harness": "codex", "account": "/home/me/.codex-lets-padel"}
        assert flow.account_label(claude, names) == "claude-b"
        assert flow.account_label(codex, names) == "codex-a"


def test_vendor_pinned_store_path_maps_through_crew_accounts():
    with tempfile.TemporaryDirectory() as tmp:
        write_accounts(tmp, ACCOUNTS)
        home = flow.Home(label="home", path=tmp, kind="captain")
        names = flow.load_account_names([home])
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
        assert flow.load_account_names([home]) == {}


def test_account_names_follow_the_file_on_disk():
    with tempfile.TemporaryDirectory() as tmp:
        entry = {"name": "claude-a", "claude_config_dir": "/home/me/.claude"}
        path = write_accounts(tmp, {"accounts": [entry]})
        home = flow.Home(label="home", path=tmp, kind="captain")
        assert flow.load_account_names([home]) == {"/home/me/.claude": "claude-a"}
        entry["name"] = "claude-b"
        path.write_text(json.dumps({"accounts": [entry]}))
        stat = path.stat()
        os.utime(path, (stat.st_atime + 5, stat.st_mtime + 5))
        assert flow.load_account_names([home]) == {"/home/me/.claude": "claude-b"}


def test_fixture_home_labels_each_ticket_with_its_account():
    home = flow.Home("captain", str(FIXTURES / "home"), "captain")
    snap = json.loads((FIXTURES / "bearings_snapshot.json").read_text())
    cols, _ = flow.make_cards(home, snap, flow.build_meta_index([home]), {}, {}, [home])
    work = {c.task: c for c in cols["underway"]}["work-1"]
    done = {c.task: c for c in cols["awaiting_merge"]}["done-2"]
    assert flow.UI.card_agent_line(work) == "claude-a\u00b7opus\u00b7xhigh"
    assert flow.UI.card_agent_line(done) == "deepseek\u00b7sonnet\u00b7medium"


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
