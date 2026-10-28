"""The board's first paint says it is loading, never that the home is empty."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FLOW = ROOT / "scripts" / "flow_tui.py"
LOCALIZATION = ROOT / "web" / "localization.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


flow = load_module("flow_tui", FLOW)
ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def _render(snap) -> str:
    ui = flow.UI()
    with contextlib.redirect_stdout(io.StringIO()):
        ui.render(snap)
    return "\n".join(ANSI.sub("", line) for line in ui.last_frame or [])


def test_first_paint_before_any_collection_says_loading():
    snap = flow.Collector().snapshot()
    assert snap.loading is True
    text = _render(snap)
    assert "Loading board…" in text
    assert "(empty)" not in text


def test_first_paint_uses_the_spanish_catalog():
    es = load_module("_deck_localization_es", LOCALIZATION).Localization("es")
    with mock.patch.object(flow, "_presentation_text", lambda message, legacy=None, **v: es.t(message, **v)):
        text = _render(flow.Collector().snapshot())
    assert "Cargando el tablero…" in text
    assert "(empty)" not in text


def test_a_crew_still_loading_names_the_crew_wait():
    home = flow.Home("captain", "/fm", "captain")
    snap = flow.Collector()._snapshot([home], home, {k: [] for k, _ in flow.COLUMNS}, {}, {}, loading=True)
    text = _render(snap)
    assert "Loading this crew…" in text
    assert "(empty)" not in text


def test_a_collected_home_without_work_shows_empty():
    home = flow.Home("captain", "/fm", "captain")
    snap = flow.Collector()._snapshot([home], home, {k: [] for k, _ in flow.COLUMNS}, {}, {})
    text = _render(snap)
    assert "(empty)" in text
    assert "Loading" not in text
