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

import yt_dlp

try:
    yt_dlp_version = yt_dlp.__version__
except AttributeError:
    yt_dlp_version = str(getattr(yt_dlp, "version", "unknown"))

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
                log.info("System opus not loaded — not needed (using FFmpegOpusAudio)")
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
    """Minimal HTTP server for Render health checks + debug info."""
    import aiohttp.web as web
    import os as _os, json as _json
    app = web.Application()

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def debug(_: web.Request) -> web.Response:
        import base64 as _b64
        info: dict[str, object] = {
            "status": "running",
            "has_token": bool(_os.environ.get("DISCORD_TOKEN")),
            "has_cookies_env": bool(_os.environ.get("YOUTUBE_COOKIES_B64")),
            "has_potoken_env": bool(_os.environ.get("YOUTUBE_POTOKEN")),
            "has_visitor_data_env": bool(_os.environ.get("YOUTUBE_VISITOR_DATA")),
            "cookies_file_exists": _os.path.isfile("cookies.txt"),
            "potoken_file_exists": _os.path.isfile("potoken.txt"),
            "yt_dlp_version": yt_dlp_version,
        }
        if info["cookies_file_exists"]:
            try:
                sz = _os.path.getsize("cookies.txt")
                info["cookies_file_size"] = sz
            except Exception:
                pass
        # Check PO Token provider plugin
        try:
            import bgutil_ytdlp_pot_provider  # type: ignore  # noqa: F401
            info["pot_provider_plugin"] = "bgutil-ytdlp-pot-provider (installed)"
        except ImportError:
            info["pot_provider_plugin"] = "not installed"
        # Show cookies env prefix (safe)
        cval = _os.environ.get("YOUTUBE_COOKIES_B64", "")
        if cval:
            info["cookies_env_prefix"] = cval[:80] + "..."
            info["cookies_env_length"] = len(cval)
            # Try to decode with padding fix
            def _try_decode(s: str) -> str | None:
                s = s.strip()
                for attempt in [s, s + "=" * (4 - len(s) % 4 if len(s) % 4 else 0),
                                s.replace("-", "+").replace("_", "/")]:
                    try:
                        return _b64.b64decode(attempt).decode("utf-8")
                    except Exception:
                        continue
                return None
            decoded = _try_decode(cval)
            if decoded:
                info["cookies_decoded_prefix"] = decoded[:100]
                try:
                    parsed = _json.loads(decoded)
                    is_list = isinstance(parsed, list)
                    info["cookies_parsed"] = f"{'list' if is_list else 'dict'}({len(parsed) if is_list else '?'})"
                except Exception:
                    info["cookies_parsed"] = "not-json"
            else:
                info["cookies_decode_failed"] = True
        return web.json_response(info)

    async def test_youtube(_: web.Request) -> web.Response:
        """Test YouTube bypass with a known video ID."""
        import traceback
        from utils.youtube_bypass import YouTubeBypass
        bypass = YouTubeBypass()
        # Direct yt-dlp test with the exact same opts
        direct = "?"
        try:
            import yt_dlp, asyncio
            loop = asyncio.get_event_loop()
            opts = bypass._opts()
            def _direct():
                clean = {k: v for k, v in opts.items() if v is not None}
                try:
                    with yt_dlp.YoutubeDL(clean) as ydl:
                        d = ydl.extract_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ", download=False)
                        return f"OK: {d.get('title','?')[:30]} url={bool(d.get('url'))}"
                except Exception as e:
                    return f"FAIL: {e}"
            direct = await loop.run_in_executor(None, _direct)
        except Exception as e:
            direct = f"EXC: {e}"

        # Now use bypass
        try:
            result = await bypass.get_audio_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            if result:
                url, info = result
                return web.json_response({
                    "ok": True,
                    "has_url": True,
                    "url_preview": url[:80],
                    "title": info.get("title", "?"),
                    "duration_text": info.get("duration_text", "?"),
                    "direct": direct,
                })
            return web.json_response({
                "ok": False,
                "error": "Bypass returned no result",
                "direct": direct,
                "cookies_file_exists": _os.path.isfile("cookies.txt"),
            })
        except Exception as e:
            return web.json_response({
                "ok": False,
                "error": str(e)[:200],
                "traceback": traceback.format_exc()[-500:],
                "direct": direct,
            })

    async def state(_: web.Request) -> web.Response:
        """Return player state for all guilds."""
        try:
            from cogs.music import players as music_players
            data = {}
            for gid, p in music_players.items():
                data[str(gid)] = {
                    "guild": p.guild.name if p.guild else "?",
                    "queue_len": len(p.queue),
                    "current_track": p.current_track.get("title") if p.current_track else None,
                    "is_playing": p.is_playing(),
                    "is_paused": p.is_paused(),
                    "vc_connected": p.voice_client.is_connected() if p.voice_client else False,
                    "vc_channel": str(p.voice_client.channel) if p.voice_client and p.voice_client.channel else None,
                    "volume": p.volume,
                    "filter": p.current_filter,
                    "repeat_mode": p.repeat_mode,
                    "last_error": p.last_error,
                    "has_text_channel": p.text_channel is not None,
                }
            return web.json_response(data)
        except Exception as e:
            return web.json_response({"error": str(e)[:200]})

    app.router.add_get("/", health)
    app.router.add_get("/debug", debug)
    app.router.add_get("/state", state)
    app.router.add_get("/test", test_youtube)
    port = int(_os.environ.get("PORT", "10000"))
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

    async with LoMaza() as bot:
        server_task = asyncio.create_task(_health_server())
        try:
            await bot.start(token)
        finally:
            server_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
