"""Contract-checker tests against the recorded Firstmate fixture home."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "fm_contract_check.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_checker():
    spec = importlib.util.spec_from_file_location("fm_contract_check", CHECK)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fm_contract_check"] = mod
    spec.loader.exec_module(mod)
    return mod


checker = load_checker()

STUBS = ("fm-bearings-snapshot.sh", "fm-captain-hold.sh", "fm-send.sh")


def _exec_stubs(home: Path) -> None:
    for name in STUBS:
        os.chmod(home / "bin" / name, 0o755)


def _copy_fixtures(tmp: Path) -> Path:
    dst = tmp / "fixtures"
    shutil.copytree(FIXTURES, dst)
    _exec_stubs(dst / "home")
    return dst


def _fail_details(report) -> str:
    return " | ".join(c["detail"] for c in report.checks if c["status"] == "fail")


def test_fixture_home_passes_contract():
    report = checker.check_home(str(FIXTURES / "home"))
    assert report.is_ok(), _fail_details(report)


def test_fixture_contract_report_is_json_ready():
    report = checker.check_home(str(FIXTURES / "home"))
    data = report.to_json()
    assert data["ok"] is True
    assert data["counts"]["fail"] == 0
    assert any(c["check"] == "snapshot.gates" for c in data["checks"])


def test_contract_flags_missing_snapshot_field():
    with tempfile.TemporaryDirectory() as tmp:
        dst = _copy_fixtures(Path(tmp))
        snap_path = dst / "bearings_snapshot.json"
        data = json.loads(snap_path.read_text())
        del data["in_flight"][0]["doing"]
        snap_path.write_text(json.dumps(data))
        report = checker.check_home(str(dst / "home"))
        assert not report.is_ok()
        assert "in_flight[0].doing" in _fail_details(report)


def test_contract_flags_missing_meta_harness():
    with tempfile.TemporaryDirectory() as tmp:
        dst = _copy_fixtures(Path(tmp))
        for meta in (dst / "home" / "state").glob("*.meta"):
            lines = [
                line
                for line in meta.read_text().splitlines()
                if not line.startswith("harness=")
            ]
            meta.write_text("\n".join(lines) + "\n")
        report = checker.check_home(str(dst / "home"))
        assert not report.is_ok()
        assert "harness" in _fail_details(report)


def test_contract_flags_bad_decision_card():
    with tempfile.TemporaryDirectory() as tmp:
        dst = _copy_fixtures(Path(tmp))
        card = dst / "home" / "state" / "decision-cards" / "decision-1.json"
        card.write_text("{not json")
        report = checker.check_home(str(dst / "home"))
        assert not report.is_ok()
        assert "decision-1.json" in _fail_details(report)


def test_contract_flags_multiple_documents_in_one_card():
    with tempfile.TemporaryDirectory() as tmp:
        dst = _copy_fixtures(Path(tmp))
        card = dst / "home" / "state" / "decision-cards" / "decision-1.json"
        text = card.read_text().rstrip("\n")
        card.write_text(text + "\n" + text + "\n")
        report = checker.check_home(str(dst / "home"))
        assert not report.is_ok()
        assert "more than one JSON document" in _fail_details(report)


def test_fixture_home_resolves_account_labels():
    report = checker.check_home(str(FIXTURES / "home"))
    by_name = {c["check"]: c for c in report.checks}
    assert by_name["account"]["status"] == "ok", by_name["account"]["detail"]
    accounts = by_name["config/crew-accounts.json"]
    assert accounts["status"] == "ok", accounts["detail"]
    schema = by_name["account schema"]
    assert schema["status"] == "warn", schema["detail"]


def test_contract_flags_renamed_account_meta_key():
    with tempfile.TemporaryDirectory() as tmp:
        dst = _copy_fixtures(Path(tmp))
        meta = dst / "home" / "state" / "done-2.meta"
        lines = [
            "account_claude_name=claude-a" if line.startswith("account_claude=") else line
            for line in meta.read_text().splitlines()
        ]
        meta.write_text("\n".join(lines) + "\n")
        report = checker.check_home(str(dst / "home"))
        failed = {c["check"] for c in report.checks if c["status"] == "fail"}
        assert "account" in failed
        assert "account_claude_name" in _fail_details(report)


def test_contract_runs_the_upstream_account_validator():
    with tempfile.TemporaryDirectory() as tmp:
        dst = _copy_fixtures(Path(tmp))
        script = dst / "home" / "bin" / "fm-account-choose.sh"
        script.write_text("#!/usr/bin/env bash\nexit 0\n")
        os.chmod(script, 0o755)
        report = checker.check_home(str(dst / "home"))
        by_name = {c["check"]: c for c in report.checks}
        assert by_name["account schema"]["status"] == "ok", by_name["account schema"]["detail"]
        script.write_text("#!/usr/bin/env bash\nexit 1\n")
        report = checker.check_home(str(dst / "home"))
        by_name = {c["check"]: c for c in report.checks}
        assert by_name["account schema"]["status"] == "fail"


def test_contract_flags_renamed_account_store_key():
    with tempfile.TemporaryDirectory() as tmp:
        dst = _copy_fixtures(Path(tmp))
        path = dst / "home" / "config" / "crew-accounts.json"
        data = json.loads(path.read_text())
        entry = data["accounts"][0]
        entry["claude_home_dir"] = entry.pop("claude_config_dir")
        path.write_text(json.dumps(data))
        report = checker.check_home(str(dst / "home"))
        failed = {c["check"] for c in report.checks if c["status"] == "fail"}
        assert "config/crew-accounts.json" in failed


def test_contract_warns_on_malformed_account_store():
    with tempfile.TemporaryDirectory() as tmp:
        dst = _copy_fixtures(Path(tmp))
        path = dst / "home" / "config" / "crew-accounts.json"
        path.write_text('{"accounts": true}')
        report = checker.check_home(str(dst / "home"))
        assert report.is_ok()
        assert any(
            c["check"] == "config/crew-accounts.json" and c["status"] == "warn"
            for c in report.checks
        )


def test_contract_tolerates_fresh_home_without_state():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        home = tmp_path / "home"
        shutil.copytree(FIXTURES / "home" / "bin", home / "bin")
        _exec_stubs(home)
        (tmp_path / "bearings_snapshot.json").write_bytes(
            (FIXTURES / "bearings_snapshot.json").read_bytes()
        )
        report = checker.check_home(str(home))
        assert report.is_ok(), _fail_details(report)
        assert any(
            c["status"] == "warn" and c["check"] == "state" for c in report.checks
        )
        assert any(
            c["status"] == "warn"
            and c["check"] == "account"
            and "no task record" in c["detail"]
            for c in report.checks
        )
