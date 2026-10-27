"""Discord HTTP Interactions proxy - FastAPI entry point.

This module replaces the WebSocket Gateway (``bot.run()``) with a
stateless HTTP endpoint that Discord calls for every slash-command
interaction.  The only persistent state is the in-process pet catalog
and the aiohttp session used for Wiki lookups.

Environment variables
---------------------
DISCORD_PUBLIC_KEY  (required) - Ed25519 public key from the Developer
                                  Portal, used to verify every request.
DISCORD_TOKEN       (required) - Bot token, passed to discord.py REST
                                  calls (e.g. ``tree.sync``).
DISCORD_GUILD_ID    (optional) - If set, slash commands are synced to
                                  this guild instead of globally.
LOG_LEVEL           (optional) - DEBUG / INFO / WARNING / ERROR /
                                  CRITICAL (default: INFO).
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Final

import discord
from fastapi import FastAPI, HTTPException, Request, Response

from wiz_hatch.bot import Settings, WizHatchBot, configure_logging

LOGGER: Final = logging.getLogger("wiz_hatch.interactions")

# Discord interaction type constants
_INTERACTION_PING: Final = 1
_INTERACTION_APPLICATION_COMMAND: Final = 2

# Discord interaction response type constants
_RESPONSE_PONG: Final = 1
_RESPONSE_DEFERRED_CHANNEL_MESSAGE: Final = 5

# Maximum body size Discord will send (generous headroom)
_MAX_BODY_BYTES: Final = 1_048_576  # 1 MiB

# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def _verify_ed25519(public_key_hex: str, signature_hex: str, message: bytes) -> bool:
    """Return True when *message* was signed with *public_key_hex*.

    Uses PyNaCl (libsodium) for constant-time Ed25519 verification.
    """
    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyNaCl is required for signature verification") from exc

    try:
        key = VerifyKey(bytes.fromhex(public_key_hex))
        key.verify(message, bytes.fromhex(signature_hex))
    except (BadSignatureError, ValueError):
        return False
    return True


# ---------------------------------------------------------------------------
# Module-level bot instance (populated during lifespan startup)
# ---------------------------------------------------------------------------

_bot: WizHatchBot | None = None


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(*, lifespan: Any = None) -> FastAPI:
    """Build and return the FastAPI application.

    Parameters
    ----------
    lifespan:
        Optional override for the ASGI lifespan context manager.
        Pass a no-op stub in unit tests to avoid real Discord connections.
    """
    if lifespan is None:
        lifespan = _default_lifespan

    _app = FastAPI(title="wiz-hatch interactions proxy", lifespan=lifespan)

    @_app.post("/wiz-hatch")
    async def interactions_endpoint(request: Request) -> Response:
        return await _handle_interaction(request)

    @_app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @_app.get("/readyz")
    async def readyz() -> Response:
        ready = _bot is not None and _bot.is_ready()
        if ready:
            return Response(
                content='{"status":"ready"}',
                media_type="application/json",
                status_code=200,
            )
        return Response(
            content='{"status":"not_ready"}',
            media_type="application/json",
            status_code=503,
        )

    return _app


@asynccontextmanager
async def _default_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start the bot REST client; sync commands; tear down on shutdown."""
    global _bot

    settings = Settings.from_environment()
    configure_logging(settings.log_level)

    bot = WizHatchBot(settings)
    # Manually run setup_hook so the cog and command tree are populated
    # without opening a WebSocket Gateway connection.
    await bot.setup_hook()

    _bot = bot
    LOGGER.info("Discord HTTP Interactions proxy ready")

    yield

    LOGGER.info("Shutting down proxy ...")
    await bot.close()
    _bot = None


def _get_public_key() -> str:
    key = os.environ.get("DISCORD_PUBLIC_KEY", "").strip()
    if not key:
        raise RuntimeError("DISCORD_PUBLIC_KEY must be set")
    return key


# ---------------------------------------------------------------------------
# Core request handler (shared by the real app and tests)
# ---------------------------------------------------------------------------


async def _handle_interaction(request: Request) -> Response:
    """Validate signature and dispatch the Discord interaction payload."""
    import json

    # --- Read body (bounded) -----------------------------------------------
    body = await request.body()
    if len(body) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    # --- Signature verification ---------------------------------------------
    signature = request.headers.get("X-Signature-Ed25519", "")
    timestamp = request.headers.get("X-Signature-Timestamp", "")
    if not signature or not timestamp:
        raise HTTPException(status_code=401, detail="Missing signature headers")

    # Protect against replay: reject timestamps older than 5 minutes.
    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid timestamp") from None
    if abs(time.time() - ts) > 300:
        raise HTTPException(status_code=401, detail="Timestamp out of range")

    message = timestamp.encode() + body
    try:
        public_key = _get_public_key()
    except RuntimeError as exc:
        LOGGER.error("Configuration error: %s", exc)
        raise HTTPException(status_code=500, detail="Server misconfigured") from exc

    if not _verify_ed25519(public_key, signature, message):
        raise HTTPException(status_code=401, detail="Invalid request signature")

    # --- Parse JSON payload -------------------------------------------------
    try:
        payload: dict[str, Any] = json.loads(body)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid JSON") from None

    interaction_type: int = payload.get("type", 0)

    # --- PING -> PONG -------------------------------------------------------
    if interaction_type == _INTERACTION_PING:
        return Response(content='{"type":1}', media_type="application/json")

    # --- Slash commands ----------------------------------------------------
    if interaction_type == _INTERACTION_APPLICATION_COMMAND:
        if _bot is None:
            raise HTTPException(status_code=503, detail="Bot not initialised")
        return await _dispatch_application_command(payload)

    raise HTTPException(
        status_code=400,
        detail=f"Unknown interaction type: {interaction_type}",
    )


async def _dispatch_application_command(payload: dict[str, Any]) -> Response:
    """Route a slash-command payload through discord.py's interaction machinery.

    Because some handlers perform network I/O that may exceed Discord's 3-second
    window, we always return a *deferred* response (type 5) immediately.
    discord.py then delivers the real answer via the follow-up webhook URL that
    Discord embeds in every interaction payload.
    """
    bot = _bot
    if bot is None:
        raise HTTPException(status_code=503, detail="Bot not initialised")

    try:
        interaction = discord.Interaction.from_dict(  # type: ignore[attr-defined]
            payload,
            bot._connection,
        )
    except Exception:
        LOGGER.exception("Failed to build Interaction from payload")
        raise HTTPException(
            status_code=400, detail="Malformed interaction payload"
        ) from None

    try:
        await bot.tree.call_parents(interaction, interaction.namespace)  # type: ignore[attr-defined]
    except Exception:
        LOGGER.exception("Command tree dispatch raised an exception")

    return Response(
        content='{"type":5}',
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# Module-level app instance (used by Uvicorn: wiz_hatch.interactions:app)
# ---------------------------------------------------------------------------

app = create_app()
