"""
Lo Maza — Premium Discord Music Bot
Entry point: loads cogs, syncs slash commands, and starts the bot.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import logging
import os
import sys

import discord
from discord.ext import commands

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("lo-maza")


# ── Bot class ──────────────────────────────────────────────────────────────────

class LoMaza(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.guilds = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            description="Lo Maza — Premium Music Platform",
        )

    async def setup_hook(self) -> None:
        if not discord.opus.is_loaded():
            import subprocess as _sp
            candidates: list[str] = []
            dpy_bin = os.path.join(os.path.dirname(discord.__file__), "bin")
            if os.path.isdir(dpy_bin):
                for dll in os.listdir(dpy_bin):
                    if dll.lower().endswith(".dll") and "opus" in dll.lower():
                        candidates.append(os.path.join(dpy_bin, dll))
            found = ctypes.util.find_library("opus")
            if found:
                candidates.append(found)
            try:
                nix_result = _sp.run(
                    ["nix-instantiate", "--eval", "-E",
                     'with import <nixpkgs> {}; "${libopus}/lib/libopus.so"'],
                    capture_output=True, text=True, timeout=10,
                )
                nix_path = nix_result.stdout.strip().strip('"')
                if nix_path:
                    candidates.append(nix_path)
            except Exception:
                pass
            candidates += ["libopus.so.0", "libopus.so", "libopus-0.x64.dll", "libopus-0.x86.dll"]
            for path in candidates:
                try:
                    discord.opus.load_opus(path)
                    log.info("Loaded Opus: %s", path)
                    break
                except Exception:
                    continue
            else:
                log.error("Opus library not found — voice audio will not work!")
        await self.load_extension("cogs.music")
        log.info("Loaded cog: cogs.music")

    async def on_ready(self) -> None:
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id if self.user else "?")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="music | /play",
            )
        )
        asyncio.create_task(self._sync_commands())

    async def _sync_commands(self) -> None:
        await asyncio.sleep(2)
        for vc in self.voice_clients:
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
        for guild in self.guilds:
            try:
                await self.tree.sync(guild=guild)
                log.info("Synced commands to guild: %s (%s)", guild.name, guild.id)
            except Exception as e:
                log.warning("Failed to sync guild %s: %s", guild.name, e)
        try:
            await self.tree.sync()
            log.info("Synced global commands.")
        except Exception as e:
            log.warning("Failed global sync: %s", e)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("Joined guild: %s (%s)", guild.name, guild.id)
        try:
            await self.tree.sync(guild=guild)
            log.info("Synced commands to guild: %s", guild.name)
        except Exception as e:
            log.warning("Failed to sync to guild %s: %s", guild.name, e)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        log.error("Command error: %s", error, exc_info=True)

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ) -> None:
        cmd_name = interaction.command.name if interaction.command else "unknown"
        log.error("App command error in /%s: %s", cmd_name, error, exc_info=True)
        msg = "An unexpected error occurred. Please try again."
        if isinstance(error, discord.app_commands.CommandOnCooldown):
            msg = f"Slow down! Try again in {error.retry_after:.1f}s."
        elif isinstance(error, discord.app_commands.CommandNotFound):
            return
        try:
            embed = discord.Embed(description=f"❌ {msg}", color=0xED4245)
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            pass


# ── Entry point ────────────────────────────────────────────────────────────────

async def _health_server() -> None:
    """Minimal HTTP server for Render health checks."""
    import aiohttp.web as web
    app = web.Application()
    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")
    app.router.add_get("/", health)
    port = int(os.environ.get("PORT", "10000"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Health server started on port %d", port)


async def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        log.error("DISCORD_TOKEN environment variable is not set. Exiting.")
        sys.exit(1)

    async with asyncio.TaskGroup() as tg:
        tg.create_task(_health_server())
        async with LoMaza() as bot:
            await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
