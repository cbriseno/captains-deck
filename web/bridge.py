#!/usr/bin/env python3
"""Captain's Deck web bridge.

Serves the Captain's Deck board and a PWA over HTTP, and submits Captain's Call
answers through the same intake the TUI uses.

The bridge does not reimplement the board. It imports the plugin's own model
(``scripts/flow_tui.py``), so cards, columns, homes and decision dialogs never
diverge from the TUI.

Run:
    python3 bridge.py            # 127.0.0.1:8787

Environment:
    CD_WEB_BIND   bind address (default 127.0.0.1)
    CD_WEB_PORT   port (default 8787)
    FM_FLOW_SCRIPTS   plugin scripts dir
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent

# Herdr lives in ~/.local/bin for this user. A service process may start with a
# minimal PATH, so the bridge repairs it before the model shells out to `herdr`.
os.environ["PATH"] = os.environ.get("PATH", "") + ":/home/herdr/.local/bin"

# Herdr often lives in the user's own bin directory. A service process can start
# with a minimal PATH, so the bridge repairs it before the model shells out to
# `herdr`.
_local_bin = os.path.expanduser("~/.local/bin")
if _local_bin not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + _local_bin

# The model is the plugin's own scripts directory: the sibling of web/.
SCRIPTS = pathlib.Path(os.environ.get("FM_FLOW_SCRIPTS") or HERE.parent / "scripts")
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# The model lives in the plugin checkout, which is not an installed package.
import flow_tui as fm  # noqa: E402  # pyright: ignore[reportMissingImports]
import push  # noqa: E402  # pyright: ignore[reportMissingImports]

import aiohttp  # noqa: E402
from aiohttp import WSMsgType, web  # noqa: E402

STATIC = HERE / "static"
BIND = os.environ.get("CD_WEB_BIND", "127.0.0.1")
ANSWER_LIMIT = fm.ANSWER_LIMIT
DATA_DIR = pathlib.Path(
    os.environ.get("CD_WEB_DATA", str(pathlib.Path.home() / ".config" / "captains-deck-web"))
)
WATCH_SECS = 5.0


def default_vapid_subject() -> str:
    """The contact URI the VAPID token carries.

    Apple refuses a ``mailto:`` whose domain it cannot believe - a plain
    ``mailto:captain@localhost`` answers 403 BadJwtToken - while an ``https:``
    URI is accepted. The deck's own tailnet address is the honest choice: it
    is the address the app runs on and it points back at this server.
    """
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            dns = str(json.loads(result.stdout).get("Self", {}).get("DNSName") or "").strip(".")
            if dns:
                return f"https://{dns}"
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError) as exc:
        print(f"cannot read the tailnet name: {exc}", file=sys.stderr)
    return "https://localhost"


VAPID_SUBJECT = os.environ.get("CD_WEB_VAPID_SUBJECT") or default_vapid_subject()


def env_number(name: str, default, cast):
    """Read a numeric environment value; a bad value is reported, not fatal."""
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        print(f"{name}={raw!r} is not a number, using {default}", file=sys.stderr)
        return default


PORT = env_number("CD_WEB_PORT", 8787, int)
TICK_SECS = env_number("CD_WEB_TICK_SECS", 1.0, float)


# --------------------------------------------------------------------------- #
# Model -> JSON
# --------------------------------------------------------------------------- #


def as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def card_json(card) -> dict:
    out = {}
    for name in fm.Card.__slots__:
        value = getattr(card, name, "")
        if isinstance(value, float):
            value = round(value, 3)
        out[name] = value
    # A slot the model never set reads as "" through getattr, and the phone
    # needs a boolean for its "show answered" switch.
    out["answered_hidden"] = bool(out.get("answered_hidden"))
    return out


def home_json(home, snap) -> dict:
    offline = snap.offline.get(home.label) or 0.0
    return {
        "label": home.label,
        "path": home.path,
        "kind": home.kind,
        "remote": bool(fm.is_remote_home(home)),
        "aggregate": bool(fm.is_aggregate_home(home)),
        "activity": snap.activity.get(home.label, ""),
        "offline_since": round(offline, 3) if offline else 0,
        "count": snap.counts.get(home.label, 0),
    }


def board_json(snap) -> dict:
    return {
        "seq": snap.seq,
        "collected_at": round(snap.collected_at, 3),
        "loading": bool(snap.loading),
        "error": snap.error,
        "active_home": snap.home.label if snap.home is not None else "",
        "homes": [home_json(h, snap) for h in snap.homes],
        "columns": [
            {
                "id": key,
                "title": title,
                "cards": [card_json(c) for c in (snap.cols.get(key) or [])],
            }
            for key, title in fm.COLUMNS
        ],
        "totals": {k: as_int(v) for k, v in snap.totals.items()},
        "others": [card_json(c) for c in snap.others],
    }


def find_card(snap, key: str):
    for cards in snap.cols.values():
        for card in cards:
            if card.task == key:
                return card
    for card in snap.others:
        if card.task == key:
            return card
    return None


class Board:
    """Owns the plugin's Collector thread and answers request-shaped reads."""

    def __init__(self) -> None:
        self.collector = fm.Collector()
        self.lock = threading.Lock()
        self.submit_lock = threading.Lock()

    def start(self) -> None:
        if not self.collector.is_alive():
            self.collector.start()

    def stop(self) -> None:
        # Shutdown must never raise, but a silent failure would hide a stuck
        # collector, so the reason still reaches the log.
        try:
            self.collector.stop()
        except Exception as exc:  # noqa: BLE001 - shutdown must never raise
            print(f"collector stop failed: {exc}", file=sys.stderr)

    def snapshot(self):
        return self.collector.snapshot()

    def board(self, home: str = "") -> dict:
        if home:
            self.collector.set_active(home)
        return board_json(self.snapshot())

    def ticket(self, key: str) -> dict | None:
        snap = self.snapshot()
        card = find_card(snap, key)
        if card is None:
            return None
        content = fm.decision_card_content(card, snap.homes, snap.home)
        # Server-side locations stay server-side; the client needs none of them.
        content.pop("home_path", None)
        content.pop("wake_home", None)
        content["answer_open"] = self._answer_open(card)
        return content

    @staticmethod
    def _answer_open(card) -> bool:
        """Only a Captain's Call ticket takes an answer."""
        return card.bucket == "captains_call"

    def submit(self, key: str, value: str, note: str) -> tuple[bool, str, str]:
        """Record one answer through the plugin's own submit path."""
        snap = self.snapshot()
        card = find_card(snap, key)
        if card is None:
            return False, "this ticket is not on the board", ""
        if not self._answer_open(card):
            return False, "only a Captain's Call ticket takes an answer", ""
        content = fm.decision_card_content(card, snap.homes, snap.home)
        value = (value or "").strip()
        note = (note or "").strip()
        if len(note.encode("utf-8")) > ANSWER_LIMIT:
            return False, f"the note is longer than {ANSWER_LIMIT} bytes", ""
        if not value and not note:
            return False, "pick an option or write an answer", ""
        dialog = fm.DecisionDialog(content)
        if value:
            index = next(
                (i for i, opt in enumerate(dialog.options) if opt["value"] == value),
                None,
            )
            if index is None:
                return False, "this option is no longer offered", ""
            dialog.selected = index
        else:
            if not dialog.freeform:
                return False, "this call needs one of the offered options", ""
            dialog.selected = -1
        dialog.note = note
        # run_submit shells out to firstmate's intake, so it leaves the loop.
        with self.submit_lock:
            ok, detail = fm.run_submit(dialog)
        return ok, detail, dialog.display_answer()


# --------------------------------------------------------------------------- #
# Web Push
# --------------------------------------------------------------------------- #


class PushStore:
    """The devices that asked for notifications, kept in one JSON file.

    One entry per browser push subscription: the endpoint, the two keys the
    payload is encrypted to, and a short device label for the log.
    """

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.items: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"subscriptions unreadable: {exc}", file=sys.stderr)
            return
        if isinstance(data, list):
            self.items = [
                item
                for item in data
                if isinstance(item, dict) and item.get("endpoint") and item.get("p256dh")
            ]

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.items, indent=2), encoding="utf-8")
            self.path.chmod(0o600)
        except OSError as exc:
            print(f"cannot store subscriptions: {exc}", file=sys.stderr)

    def add(self, subscription: dict, device: str) -> int:
        endpoint = str(subscription.get("endpoint") or "")
        keys = subscription.get("keys") or {}
        if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
            raise ValueError("a subscription needs endpoint, keys.p256dh and keys.auth")
        with self.lock:
            self.items = [item for item in self.items if item.get("endpoint") != endpoint]
            self.items.append(
                {
                    "endpoint": endpoint,
                    "p256dh": str(keys["p256dh"]),
                    "auth": str(keys["auth"]),
                    "device": device[:80],
                    "created_at": round(time.time(), 3),
                }
            )
            self._save()
            return len(self.items)

    def remove(self, endpoint: str) -> int:
        with self.lock:
            self.items = [item for item in self.items if item.get("endpoint") != endpoint]
            self._save()
            return len(self.items)

    def list(self) -> list[dict]:
        with self.lock:
            return list(self.items)


class Pusher:
    """Encrypts one payload per device and hands it to the push service."""

    def __init__(self, store: PushStore, keys: dict[str, str], subject: str) -> None:
        self.store = store
        self.keys = keys
        self.subject = subject

    async def send(self, item: dict, payload: dict) -> int | None:
        body = push.encrypt(
            item["p256dh"], item["auth"], json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )
        headers = push.push_headers(item["endpoint"], self.subject, self.keys)
        timeout = aiohttp.ClientTimeout(total=20)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(item["endpoint"], data=body, headers=headers) as response:
                    status = response.status
                    reason = await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            print(f"push failed: {exc}", file=sys.stderr)
            return None
        # A push service forgets a subscription when the app is removed: 404 and
        # 410 mean this device is gone, so its entry goes too.
        if status in (404, 410):
            self.store.remove(item["endpoint"])
        elif status >= 300:
            # The push service explains a refusal in the body; without it a 403
            # stays a guess.
            print(
                f"push rejected: {status} for {item.get('device', '')}: {reason[:200]}",
                file=sys.stderr,
            )
        else:
            # Accepted, not necessarily delivered: the phone still has to show
            # it, so the accepted line is the point where the bridge stops.
            print(f"push accepted: {status} for {item.get('device', '')}", file=sys.stderr)
        return status

    async def send_all(self, payload: dict) -> dict:
        devices = self.store.list()
        sent = 0
        for item in devices:
            status = await self.send(item, payload)
            if status is not None and status < 300:
                sent += 1
        return {"sent": sent, "devices": len(devices)}


class CallWatcher:
    """Announce a new Captain's Call to the subscribed devices.

    The first snapshot only fills the baseline: a restart must not replay the
    calls that were already open.
    """

    def __init__(self, board: "Board", pusher: Pusher, interval: float = WATCH_SECS) -> None:
        self.board = board
        self.pusher = pusher
        self.interval = interval
        self.seen: set[str] | None = None

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a watcher failure must not stop the bridge
                print(f"call watcher: {exc}", file=sys.stderr)

    async def tick(self) -> list[str]:
        snap = self.board.snapshot()
        calls = {card.task: card for card in (snap.cols.get("captains_call") or [])}
        if self.seen is None:
            self.seen = set(calls)
            return []
        fresh = [key for key in calls if key not in self.seen]
        self.seen = set(calls)
        if not fresh or not self.pusher.store.list():
            return fresh
        count = len(calls)
        for key in fresh[:3]:
            card = calls[key]
            await self.pusher.send_all(
                {
                    "title": "Captain's Call",
                    "body": card.title or key,
                    "tag": "call:" + key,
                    "url": "/?ticket=" + urllib.parse.quote(key),
                    "count": count,
                }
            )
        return fresh


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


async def handle_board(request: web.Request) -> web.Response:
    board: Board = request.app["board"]
    return web.json_response(board.board(request.query.get("home", "")))


async def handle_ticket(request: web.Request) -> web.Response:
    board: Board = request.app["board"]
    ticket = board.ticket(request.match_info["key"])
    if ticket is None:
        return web.json_response({"error": "not on the board"}, status=404)
    return web.json_response(ticket)


async def handle_answer(request: web.Request) -> web.Response:
    board: Board = request.app["board"]
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "invalid JSON"}, status=400)
    key = request.match_info["key"]
    ok, detail, answer = await asyncio.to_thread(
        board.submit,
        key,
        str(body.get("value") or ""),
        str(body.get("note") or ""),
    )
    payload = {"ok": ok, "detail": detail, "answer": answer}
    return web.json_response(payload, status=200 if ok else 409)


async def send_board(ws: web.WebSocketResponse, board: Board) -> bool:
    """Send one frame; a closed socket ends the loop instead of raising."""
    if ws.closed:
        return False
    try:
        await ws.send_json({"type": "board", "board": board_json(board.snapshot())})
        return True
    except (ConnectionResetError, RuntimeError):
        return False


async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    board: Board = request.app["board"]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    sent_seq = -1
    home = request.query.get("home", "")
    if home:
        board.collector.set_active(home)
    try:
        snap = board.snapshot()
        sent_seq = snap.seq
        if not await send_board(ws, board):
            return ws
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=TICK_SECS)
            except asyncio.TimeoutError:
                snap = board.snapshot()
                if snap.seq != sent_seq:
                    sent_seq = snap.seq
                    if not await send_board(ws, board):
                        break
                continue
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                kind = data.get("type")
                if kind == "home":
                    board.collector.set_active(str(data.get("label") or ""))
                elif kind == "refresh":
                    board.collector.refresh_now()
                elif kind == "ticket":
                    ticket = board.ticket(str(data.get("key") or ""))
                    if not ws.closed:
                        try:
                            await ws.send_json(
                                {"type": "ticket", "key": data.get("key"), "ticket": ticket}
                            )
                        except (ConnectionResetError, RuntimeError):
                            break
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                break
    finally:
        if not ws.closed:
            await ws.close()
    return ws


async def handle_push_key(request: web.Request) -> web.Response:
    return web.json_response({"public_key": request.app["vapid"]["public_key"]})


async def handle_push_status(request: web.Request) -> web.Response:
    devices = request.app["store"].list()
    return web.json_response(
        {
            "devices": len(devices),
            "labels": [item.get("device", "") for item in devices],
        }
    )


async def handle_push_subscribe(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "a subscription object is expected"}, status=400)
    try:
        count = request.app["store"].add(
            body.get("subscription") or {}, str(body.get("device") or "")
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"ok": True, "devices": count})


async def handle_push_unsubscribe(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "invalid JSON"}, status=400)
    endpoint = str((body or {}).get("endpoint") or "")
    return web.json_response({"ok": True, "devices": request.app["store"].remove(endpoint)})


async def handle_push_test(request: web.Request) -> web.Response:
    result = await request.app["pusher"].send_all(
        {
            "title": "Captain's Deck",
            "body": "Notifications reach this device.",
            # A fresh tag per press: an existing notification with the same tag
            # is replaced in place, which reads as "nothing happened".
            "tag": f"deck-test:{round(time.time())}",
            "url": "/",
            "count": 0,
        }
    )
    return web.json_response({"ok": True, **result})


async def handle_static(request: web.Request) -> web.StreamResponse:
    rel = request.match_info.get("path") or "index.html"
    target = (STATIC / rel).resolve()
    if STATIC not in target.parents and target != STATIC:
        raise web.HTTPForbidden()
    if not target.is_file():
        raise web.HTTPNotFound()
    response = web.FileResponse(target)
    # The shell must revalidate: an installed app otherwise keeps a cached copy
    # for days, and the deck would look unchanged after an update.
    response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


async def on_startup(app: web.Application) -> None:
    app["board"].start()
    app["watcher_task"] = asyncio.create_task(app["watcher"].run())


async def on_cleanup(app: web.Application) -> None:
    task = app.get("watcher_task")
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    app["board"].stop()


def make_app() -> web.Application:
    app = web.Application()
    board = Board()
    store = PushStore(DATA_DIR / "subscriptions.json")
    vapid = push.load_or_create_keys(DATA_DIR / "vapid.json")
    app["board"] = board
    app["store"] = store
    app["vapid"] = vapid
    app["pusher"] = Pusher(store, vapid, VAPID_SUBJECT)
    app["watcher"] = CallWatcher(board, app["pusher"])
    app.add_routes(
        [
            web.get("/api/board", handle_board),
            web.get("/api/ticket/{key}", handle_ticket),
            web.post("/api/ticket/{key}/answer", handle_answer),
            web.get("/api/push/key", handle_push_key),
            web.get("/api/push/status", handle_push_status),
            web.post("/api/push/subscribe", handle_push_subscribe),
            web.post("/api/push/unsubscribe", handle_push_unsubscribe),
            web.post("/api/push/test", handle_push_test),
            web.get("/ws", handle_ws),
            web.get("/{path:.*}", handle_static),
        ]
    )
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> int:
    if not (STATIC / "index.html").is_file():
        print(f"missing {STATIC / 'index.html'}", file=sys.stderr)
        return 2
    print(f"captain's deck bridge: http://{BIND}:{PORT}", flush=True)
    print(f"push data: {DATA_DIR}", flush=True)
    print(f"vapid subject: {VAPID_SUBJECT}", flush=True)
    web.run_app(make_app(), host=BIND, port=PORT, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
