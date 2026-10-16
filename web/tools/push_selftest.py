#!/usr/bin/env python3
"""End-to-end check of the push path, without Apple or Google.

Starts a stand-in push service on loopback, subscribes it through the running
bridge, asks the bridge to send a test notification, and then verifies what
arrived:

* the ``Authorization: vapid t=..., k=...`` header carries a JWT that the
  bridge's own public key verifies, and
* the body decrypts to the payload that was sent.

Run it from the project root:

    python3 tools/push_selftest.py [--bridge http://127.0.0.1:8787]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402

import push  # noqa: E402

RECEIVED: asyncio.Queue = asyncio.Queue()


def verify_vapid(authorization: str, public_key_b64: str) -> dict:
    """Check the JWT the bridge signed, exactly as a push service would."""
    if not authorization.startswith("vapid "):
        raise ValueError("no vapid authorization header")
    fields = dict(part.split("=", 1) for part in authorization[6:].split(",") if "=" in part)
    token = fields.get("t", "")
    header, claims, signature = token.split(".")
    public = push.public_key_from_raw(push.b64url_decode(public_key_b64))
    raw = push.b64url_decode(signature)
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:], "big")
    public.verify(
        encode_dss_signature(r, s),
        f"{header}.{claims}".encode("ascii"),
        ec.ECDSA(hashes.SHA256()),
    )
    try:
        parsed = json.loads(push.b64url_decode(claims))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"the JWT claims are not readable: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("the JWT claims are not an object")
    return parsed


RFC_VECTOR = {
    "ua_private": "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94",
    "auth_secret": "BTBZMqHH6r4Tts7J_aSIgg",
    "record": (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
        "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT"
        "pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
    ),
    "plaintext": "When I grow up, I want to be a watermelon",
}


def check_vector() -> bool:
    """RFC 8291 Appendix A: a payload this bridge must be able to read.

    A round trip on its own proves nothing, because an encryptor and its own
    decryptor can share the same wrong derivation. On 2026-10-16 they did: every
    push was dropped by the phone while the push service still answered 201.
    This vector is the check that catches it.
    """
    private = ec.derive_private_key(
        int.from_bytes(push.b64url_decode(RFC_VECTOR["ua_private"]), "big"), ec.SECP256R1()
    )
    try:
        plain = push.decrypt_for_test(
            push.b64url_decode(RFC_VECTOR["record"]), private, RFC_VECTOR["auth_secret"]
        )
    except Exception as exc:  # noqa: BLE001 - the report is the point
        print(f"FAIL: RFC 8291 vector: {type(exc).__name__} {exc}")
        return False
    text = plain.rstrip(b"\x02").decode()
    if text != RFC_VECTOR["plaintext"]:
        print(f"FAIL: RFC 8291 vector decrypts to {text!r}")
        return False
    print("rfc 8291 vector: ok")
    return True


async def handle_push(request: web.Request) -> web.Response:
    body = await request.read()
    await RECEIVED.put({"headers": dict(request.headers), "body": body})
    return web.Response(status=201)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge", default="http://127.0.0.1:8787")
    parser.add_argument("--port", type=int, default=8899)
    args = parser.parse_args()

    app = web.Application()
    app.router.add_post("/push/{token}", handle_push)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", args.port)
    await site.start()

    # A stand-in device key pair: this is what a real browser would own.
    device = ec.generate_private_key(ec.SECP256R1())
    subscription = {
        "endpoint": f"http://127.0.0.1:{args.port}/push/selftest",
        "keys": {
            "p256dh": push.b64url(push.public_bytes(device.public_key())),
            "auth": push.b64url(b"selftest-auth-16"),
        },
    }

    failures = 0
    if not check_vector():
        failures += 1
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{args.bridge}/api/push/key") as response:
            public_key = (await response.json())["public_key"]
        print(f"bridge VAPID public key: {len(push.b64url_decode(public_key))} bytes")

        async with session.post(
            f"{args.bridge}/api/push/subscribe",
            json={"subscription": subscription, "device": "selftest"},
        ) as response:
            status = await response.json()
        print(f"subscribe: {response.status} {status}")
        failures += 0 if response.status == 200 else 1

        async with session.post(f"{args.bridge}/api/push/test") as response:
            result = await response.json()
        print(f"test send: {response.status} {result}")
        # A real phone may be subscribed too, so one is the floor, not the count.
        failures += 0 if result.get("sent", 0) >= 1 else 1

        try:
            received = await asyncio.wait_for(RECEIVED.get(), timeout=10)
        except asyncio.TimeoutError:
            print("FAIL: nothing arrived at the stand-in push service")
            received = None
            failures += 1

        if received:
            try:
                claims = verify_vapid(received["headers"]["Authorization"], public_key)
                print(f"vapid claims: aud={claims['aud']} sub={claims['sub']}")
            except Exception as exc:  # noqa: BLE001 - the report is the point
                print(f"FAIL: vapid verification: {exc}")
                failures += 1
            try:
                payload = json.loads(
                    push.decrypt_for_test(
                        received["body"],
                        device,
                        subscription["keys"]["auth"],
                    ).rstrip(b"\x02")
                )
                print(f"decrypted payload: {payload}")
            except Exception as exc:  # noqa: BLE001 - the report is the point
                print(f"FAIL: payload decryption: {exc}")
                failures += 1
            print(f"content-encoding: {received['headers'].get('Content-Encoding')}")

        async with session.post(
            f"{args.bridge}/api/push/unsubscribe",
            json={"endpoint": subscription["endpoint"]},
        ) as response:
            print(f"unsubscribe: {response.status} {await response.json()}")

    await runner.cleanup()
    print("RESULT:", "ok" if failures == 0 else f"{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
