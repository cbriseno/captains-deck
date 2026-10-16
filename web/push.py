#!/usr/bin/env python3
"""Web Push for the Captain's Deck bridge.

Implements the two halves a push service needs:

* VAPID (RFC 8292) - a signed JWT that identifies this server to Apple or
  Google, built from a P-256 key pair the bridge owns.
* Message encryption (RFC 8291, ``aes128gcm``) - the payload is encrypted to
  the subscription's public key, so the push service carries ciphertext only.

No third-party package is used: ``cryptography`` and the standard library
cover both.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import struct
import sys
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

CURVE = ec.SECP256R1()
PUBLIC_KEY_BYTES = 65
RECORD_SIZE = 4096
VAPID_TTL_SECONDS = 12 * 3600


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def public_key_from_raw(raw: bytes) -> ec.EllipticCurvePublicKey:
    return ec.EllipticCurvePublicKey.from_encoded_point(CURVE, raw)


# --------------------------------------------------------------------------- #
# VAPID key pair
# --------------------------------------------------------------------------- #


def generate_keys() -> dict[str, str]:
    private = ec.generate_private_key(CURVE)
    private_raw = private.private_numbers().private_value.to_bytes(32, "big")
    return {
        "private_key": b64url(private_raw),
        "public_key": b64url(public_bytes(private.public_key())),
    }


def load_or_create_keys(path: str | os.PathLike[str]) -> dict[str, str]:
    """Read the VAPID pair, and make one on first use.

    The pair is the server's identity to the push services. A new pair makes
    every existing subscription undeliverable, so the file is written once and
    then kept.
    """
    target = pathlib.Path(path).expanduser()
    data: object = {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"VAPID key file unreadable: {exc}", file=sys.stderr)
        data = {}
    if isinstance(data, dict) and data.get("private_key") and data.get("public_key"):
        return data
    keys = generate_keys()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(keys, indent=2), encoding="utf-8")
        target.chmod(0o600)
    except OSError as exc:
        print(f"cannot store the VAPID pair: {exc}", file=sys.stderr)
    return keys


def private_key_from(keys: dict[str, str]) -> ec.EllipticCurvePrivateKey:
    value = int.from_bytes(b64url_decode(keys["private_key"]), "big")
    return ec.derive_private_key(value, CURVE)


# --------------------------------------------------------------------------- #
# VAPID authorization
# --------------------------------------------------------------------------- #


def _raw_signature(der: bytes) -> bytes:
    """DER ECDSA is variable length; JWS wants the fixed r||s form."""
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def vapid_token(endpoint: str, subject: str, keys: dict[str, str], now: float = 0) -> str:
    """A VAPID JWT for one push endpoint."""
    parts = endpoint.split("/")
    if len(parts) < 3:
        raise ValueError(f"push endpoint has no origin: {endpoint!r}")
    audience = parts[0] + "//" + parts[2]
    header = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    claims = b64url(
        json.dumps(
            {
                "aud": audience,
                "exp": round((now or time.time()) + VAPID_TTL_SECONDS),
                "sub": subject,
            }
        ).encode()
    )
    signing_input = f"{header}.{claims}".encode("ascii")
    signature = private_key_from(keys).sign(signing_input, ec.ECDSA(hashes.SHA256()))
    return f"{header}.{claims}.{b64url(_raw_signature(signature))}"


def vapid_headers(endpoint: str, subject: str, keys: dict[str, str]) -> dict[str, str]:
    return {
        "Authorization": f"vapid t={vapid_token(endpoint, subject, keys)}, k={keys['public_key']}"
    }


# --------------------------------------------------------------------------- #
# Payload encryption (RFC 8291, aes128gcm)
# --------------------------------------------------------------------------- #


def _hkdf(secret: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(secret)


def encrypt(p256dh: str, auth: str, payload: bytes) -> bytes:
    """Encrypt one push payload for one subscription."""
    user_public = b64url_decode(p256dh)
    auth_secret = b64url_decode(auth)
    if len(user_public) != PUBLIC_KEY_BYTES:
        raise ValueError("the subscription key is not an uncompressed P-256 point")

    server_private = ec.generate_private_key(CURVE)
    server_public = public_bytes(server_private.public_key())
    shared = server_private.exchange(ec.ECDH(), public_key_from_raw(user_public))

    # RFC 8291 3.4: Extract(salt=auth_secret, ikm=shared), then Expand over
    # "WebPush: info" || 0x00 || ua_public || as_public. One HKDF call is both
    # steps. The separate Extract this code used to add produced a content key
    # the device could not reproduce, and iOS drops such a message silently: the
    # push service still answers 201.
    ikm = _hkdf(
        shared, auth_secret, b"WebPush: info\x00" + user_public + server_public, 32
    )

    salt = os.urandom(16)
    content_key = _hkdf(ikm, salt, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(ikm, salt, b"Content-Encoding: nonce\x00", 12)

    # 0x02 marks the last record, so the receiver knows the payload ends here.
    body = (
        salt
        + struct.pack(">L", RECORD_SIZE)
        + bytes([len(server_public)])
        + server_public
        + AESGCM(content_key).encrypt(nonce, payload + b"\x02", None)
    )
    return body


def decrypt_for_test(body: bytes, private_key: ec.EllipticCurvePrivateKey, auth: str) -> bytes:
    """The receiving side of :func:`encrypt`. Used by the round-trip test."""
    user_public = public_bytes(private_key.public_key())
    salt = body[:16]
    idlen = body[20]
    server_public = body[21 : 21 + idlen]
    ciphertext = body[21 + idlen :]
    shared = private_key.exchange(ec.ECDH(), public_key_from_raw(server_public))
    ikm = _hkdf(
        shared,
        b64url_decode(auth),
        b"WebPush: info\x00" + user_public + server_public,
        32,
    )
    content_key = _hkdf(ikm, salt, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(ikm, salt, b"Content-Encoding: nonce\x00", 12)
    return AESGCM(content_key).decrypt(nonce, ciphertext, None)


def push_headers(
    endpoint: str, subject: str, keys: dict[str, str], ttl: int = 86400, urgency: str = "normal"
) -> dict[str, str]:
    headers = {
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(ttl),
        "Urgency": urgency,
    }
    headers.update(vapid_headers(endpoint, subject, keys))
    return headers
