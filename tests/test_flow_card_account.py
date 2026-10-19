"""Card account labels: which subscription store runs a ticket."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

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


def test_named_account_wins():
    label = flow.account_label({"harness": "claude", "account": "claude-b"}, {})
    assert label == "claude-b"


def test_vendor_pin_names_the_account_when_account_is_a_store():
    meta = {
        "harness": "claude",
        "account": "/home/me/.claude",
        "account_claude": "claude-a",
        "claude_config_dir": "/home/me/.claude",
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


def test_store_path_maps_through_crew_accounts():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config"
        cfg.mkdir()
        (cfg / "crew-accounts.json").write_text(
            json.dumps(
                {
                    "accounts": [
                        {"name": "claude-b", "claude_config_dir": "/home/me/.claude-karolina"},
                        {"name": "codex-a", "codex_home": "/home/me/.codex-lets-padel"},
                    ]
                }
            )
        )
        home = flow.Home(label="home", path=tmp, kind="captain")
        names = flow.load_account_names([home])
        meta = {
            "harness": "claude",
            "account": "/home/me/.claude-karolina",
            "claude_config_dir": "/home/me/.claude-karolina",
        }
        assert flow.account_label(meta, names) == "claude-b"


def test_unknown_record_falls_back_to_the_harness():
    meta = {"harness": "codex", "account": ""}
    assert flow.account_label(meta, {}) == ""


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
