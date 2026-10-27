"""Tests for the Discord HTTP Interactions proxy (wiz_hatch.interactions)."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Deterministic Ed25519 key-pair (generated offline; test-only)
# ---------------------------------------------------------------------------
# Private key seed (32 bytes, hex):
_TEST_PRIVATE_KEY_HEX = (
    "9d61b19deffd5a60ba844af492ec2cc44449578fffcafe55ef7c1f8c35c8c3c2"
)
# Corresponding public key (32 bytes, hex) - derived from the seed above:
_TEST_PUBLIC_KEY_HEX = (
    "87f12d4ab3d82c8ff9f6a970c1babef5320e8b58b3cc23d3828132f1b559aba4"
)


def _sign(timestamp: str, body: bytes) -> str:
    """Return the hex Ed25519 signature for *timestamp + body*."""
    from nacl.signing import SigningKey  # type: ignore[import-untyped]

    sk = SigningKey(bytes.fromhex(_TEST_PRIVATE_KEY_HEX))
    message = timestamp.encode() + body
    return sk.sign(message).signature.hex()


# ---------------------------------------------------------------------------
# No-op lifespan - avoids real Discord connections in tests
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


# ---------------------------------------------------------------------------
# App & client fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Return a FastAPI app with a no-op lifespan and the test public key."""
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", _TEST_PUBLIC_KEY_HEX)
    from wiz_hatch.interactions import create_app

    return create_app(lifespan=_noop_lifespan)


@pytest.fixture()
def client(test_app: FastAPI) -> TestClient:
    """Return a TestClient backed by the stubbed app."""
    with TestClient(test_app, raise_server_exceptions=True) as c:
        return c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post(
    client: TestClient,
    payload: dict[str, Any],
    *,
    timestamp: str | None = None,
    bad_sig: bool = False,
) -> Any:
    """POST a signed (or deliberately bad) interaction payload."""
    body = json.dumps(payload).encode()
    ts = timestamp or str(int(time.time()))
    sig = _sign(ts, body)
    if bad_sig:
        sig = "00" * 64  # invalid

    return client.post(
        "/wiz-hatch",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Signature-Ed25519": sig,
            "X-Signature-Timestamp": ts,
        },
    )


# ---------------------------------------------------------------------------
# Signature verification tests
# ---------------------------------------------------------------------------


class TestSignatureVerification:
    def test_missing_signature_header_returns_401(self, client: TestClient) -> None:
        resp = client.post(
            "/wiz-hatch",
            json={"type": 1},
            headers={"X-Signature-Timestamp": str(int(time.time()))},
        )
        assert resp.status_code == 401

    def test_missing_timestamp_header_returns_401(self, client: TestClient) -> None:
        resp = client.post(
            "/wiz-hatch",
            json={"type": 1},
            headers={"X-Signature-Ed25519": "00" * 64},
        )
        assert resp.status_code == 401

    def test_invalid_signature_returns_401(self, client: TestClient) -> None:
        resp = _post(client, {"type": 1}, bad_sig=True)
        assert resp.status_code == 401

    def test_stale_timestamp_returns_401(self, client: TestClient) -> None:
        old_ts = str(int(time.time()) - 400)  # 400 s ago - outside 5-min window
        resp = _post(client, {"type": 1}, timestamp=old_ts)
        assert resp.status_code == 401

    def test_non_integer_timestamp_returns_401(self, client: TestClient) -> None:
        body = b'{"type":1}'
        resp = client.post(
            "/wiz-hatch",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Signature-Ed25519": "00" * 64,
                "X-Signature-Timestamp": "not-a-number",
            },
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PING -> PONG tests
# ---------------------------------------------------------------------------


class TestPing:
    def test_ping_returns_pong(self, client: TestClient) -> None:
        resp = _post(client, {"type": 1})
        assert resp.status_code == 200
        assert resp.json() == {"type": 1}

    def test_ping_content_type_is_json(self, client: TestClient) -> None:
        resp = _post(client, {"type": 1})
        assert "application/json" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Health / readiness probe tests
# ---------------------------------------------------------------------------


class TestProbes:
    def test_healthz_always_200(self, client: TestClient) -> None:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_readyz_503_when_bot_not_ready(self, client: TestClient) -> None:
        from wiz_hatch import interactions as mod

        with patch.object(mod, "_bot", None):
            resp = client.get("/readyz")
            assert resp.status_code == 503

    def test_readyz_200_when_bot_ready(self, client: TestClient) -> None:
        from wiz_hatch import interactions as mod

        mock_bot = MagicMock()
        mock_bot.is_ready.return_value = True

        with patch.object(mod, "_bot", mock_bot):
            resp = client.get("/readyz")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ready"


# ---------------------------------------------------------------------------
# Unknown interaction type
# ---------------------------------------------------------------------------


class TestUnknownType:
    def test_unknown_interaction_type_returns_400(self, client: TestClient) -> None:
        resp = _post(client, {"type": 99})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# _verify_ed25519 unit tests (pure function, no HTTP)
# ---------------------------------------------------------------------------


class TestVerifyEd25519:
    def test_valid_signature_returns_true(self) -> None:
        from nacl.signing import SigningKey  # type: ignore[import-untyped]

        from wiz_hatch.interactions import _verify_ed25519

        sk = SigningKey(bytes.fromhex(_TEST_PRIVATE_KEY_HEX))
        msg = b"hello world"
        sig = sk.sign(msg).signature.hex()
        assert _verify_ed25519(_TEST_PUBLIC_KEY_HEX, sig, msg) is True

    def test_tampered_message_returns_false(self) -> None:
        from nacl.signing import SigningKey  # type: ignore[import-untyped]

        from wiz_hatch.interactions import _verify_ed25519

        sk = SigningKey(bytes.fromhex(_TEST_PRIVATE_KEY_HEX))
        msg = b"hello world"
        sig = sk.sign(msg).signature.hex()
        assert _verify_ed25519(_TEST_PUBLIC_KEY_HEX, sig, b"tampered") is False

    def test_bad_hex_signature_returns_false(self) -> None:
        from wiz_hatch.interactions import _verify_ed25519

        assert _verify_ed25519(_TEST_PUBLIC_KEY_HEX, "zz" * 32, b"msg") is False
