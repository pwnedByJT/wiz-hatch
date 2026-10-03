"""Discord bot process entry point."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Final

import discord
from discord import app_commands
from discord.ext import commands

from wiz_hatch.health import HealthServer

LOGGER: Final = logging.getLogger("wiz_hatch")
DEFAULT_HEALTH_HOST: Final = "0.0.0.0"  # noqa: S104 - container probe listener


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated process settings sourced from environment variables."""

    token: str
    guild_id: int | None
    log_level: str
    health_host: str
    health_port: int

    @classmethod
    def from_environment(cls) -> Settings:
        """Build settings without reading local dotenv files or printing secrets."""
        token = os.environ.get("DISCORD_TOKEN", "").strip()
        if not token:
            raise RuntimeError("DISCORD_TOKEN must be set")

        guild_id = _optional_snowflake(os.environ.get("DISCORD_GUILD_ID", ""))
        log_level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise RuntimeError(
                "LOG_LEVEL must be CRITICAL, ERROR, WARNING, INFO, or DEBUG"
            )

        health_host = os.environ.get("HEALTH_HOST", DEFAULT_HEALTH_HOST).strip()
        if not health_host:
            raise RuntimeError("HEALTH_HOST cannot be empty")
        health_port = _port(os.environ.get("HEALTH_PORT", "8080"))
        return cls(token, guild_id, log_level, health_host, health_port)


class WizHatchBot(commands.Bot):
    """Bot with deterministic command registration and probe lifecycle."""

    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.none()
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.settings = settings
        self.health_server = HealthServer(
            settings.health_host,
            settings.health_port,
            self.is_ready,
        )

    async def setup_hook(self) -> None:
        """Load commands, start probes, and synchronize the command tree."""
        await self.load_extension("wiz_hatch.cogs.w101_hatch")
        await self.health_server.start()

        if self.settings.guild_id is not None:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            commands_synced = await self.tree.sync(guild=guild)
            LOGGER.info(
                "Synchronized %d command(s) to development guild %d",
                len(commands_synced),
                self.settings.guild_id,
            )
        else:
            commands_synced = await self.tree.sync()
            LOGGER.info("Synchronized %d global command(s)", len(commands_synced))

    async def close(self) -> None:
        """Close the probe socket before closing the Discord connection."""
        await self.health_server.close()
        await super().close()

    async def on_ready(self) -> None:
        """Log a non-sensitive connection summary."""
        if self.user is not None:
            LOGGER.info("Connected to Discord as %s (%d)", self.user, self.user.id)


def create_bot(settings: Settings) -> WizHatchBot:
    """Create a configured bot and install a safe command error handler."""
    bot = WizHatchBot(settings)

    @bot.tree.error
    async def on_app_command_error(
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        LOGGER.error(
            "Application command failed: %s",
            type(error).__name__,
            exc_info=(type(error), error, error.__traceback__),
        )
        message = "The command could not be completed. Please try again."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    return bot


def configure_logging(level: str) -> None:
    """Configure concise UTC-compatible process logs."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main() -> None:
    """Run the bot until the process receives a shutdown signal."""
    settings = Settings.from_environment()
    configure_logging(settings.log_level)
    bot = create_bot(settings)
    bot.run(settings.token, log_handler=None)


def _optional_snowflake(value: str) -> int | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        snowflake = int(normalized)
    except ValueError as error:
        raise RuntimeError("DISCORD_GUILD_ID must be a positive integer") from error
    if not 0 < snowflake < 2**64:
        raise RuntimeError("DISCORD_GUILD_ID must be a valid Discord snowflake")
    return snowflake


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise RuntimeError("HEALTH_PORT must be an integer") from error
    if not 1 <= port <= 65_535:
        raise RuntimeError("HEALTH_PORT must be between 1 and 65535")
    return port


if __name__ == "__main__":
    main()
