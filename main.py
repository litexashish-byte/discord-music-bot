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
        import traceback, base64 as _b64
        # Check cookies file content
        cookies_file_ok = _os.path.isfile("cookies.txt")
        cookies_file_content = ""
        if cookies_file_ok:
            try:
                with open("cookies.txt", "r", encoding="utf-8") as f:
                    cookies_file_content = f.read(300)
            except Exception:
                pass
        # Check env var
        env_val = _os.environ.get("YOUTUBE_COOKIES_B64", "")
        env_prefix = env_val[:60] if env_val else ""
        # Manually test cookie writing
        manual_cookie_result = "not_tried"
        try:
            from utils.youtube_bypass import YouTubeBypass
            cookie_opts = YouTubeBypass._load_cookies()
            manual_cookie_result = f"cookies_file_exists={_os.path.isfile('cookies.txt')}, opts_keys={list(cookie_opts.keys())}, opts_cookiefile={cookie_opts.get('cookiefile', '')}"
        except Exception as e:
            manual_cookie_result = f"error={e}"
        # Try yt-dlp directly with multiple format options
        yt_result = "not_tried"
        try:
            import yt_dlp
            loop = asyncio.get_event_loop()
            format_tests = [
                "bestaudio[ext=m4a]/bestaudio/best",
                "bestaudio/best",
                "worstaudio/worst",
                "bestaudio[protocol=m3u8_native]/bestaudio/best",
                "worstaudio",
            ]
            yt_results = []
            for fmt in format_tests:
                def _test(fmt=fmt):
                    opts = {
                        "format": fmt,
                        "quiet": True,
                        "no_warnings": True,
                        "nocheckcertificate": True,
                        "cookiefile": "cookies.txt",
                        "http_headers": {
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
                        },
                    }
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        try:
                            data = ydl.extract_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ", download=False)
                            if data:
                                return f"OK: title={data.get('title','?')[:30]}, has_url={bool(data.get('url'))}"
                        except Exception as e:
                            return f"FAIL: {str(e)[:100]}"
                        return "no_data"
                r = await loop.run_in_executor(None, _test)
                yt_results.append(f"{fmt}=>{r}")
            yt_result = " | ".join(yt_results)
        except Exception as e:
            import traceback
            yt_result = f"EXCEPTION: {str(e)[:200]} | {traceback.format_exc()[-300:]}"

        # Test with EXACT bypass options to find which setting breaks it
        bypass_debug = "not_tried"
        try:
            import yt_dlp
            from utils.youtube_bypass import YouTubeBypass
            b = YouTubeBypass()
            loop = asyncio.get_event_loop()
            # Test each setting individually
            sopts = dict(b._stream_opts)
            setting_tests = {
                "base_clean": {
                    "format": "bestaudio/best", "quiet": True, "no_warnings": True,
                    "nocheckcertificate": True, "cookiefile": "cookies.txt",
                },
                "stream_opts": sopts,
                "no_headers": {k: v for k, v in sopts.items() if k != "http_headers"},
                "ua_only": {**{k: v for k, v in sopts.items() if k != "http_headers"}, "http_headers": {"User-Agent": "Mozilla/5.0 Chrome/125.0"}},
                "no_accept_enc": {**sopts, "http_headers": {k: v for k, v in sopts.get("http_headers", {}).items() if k != "Accept-Encoding"}},
                "keys_check": str(sorted(sopts.keys())),
            }
            bypass_debug_results = []
            for name, opts in setting_tests.items():
                def _test(setting_opts=opts, name=name):
                    try:
                        with yt_dlp.YoutubeDL(setting_opts) as ydl:
                            data = ydl.extract_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ", download=False)
                            if data:
                                return f"{name}:OK url={bool(data.get('url'))}"
                            return f"{name}:no_data"
                    except Exception as e:
                        return f"{name}:FAIL {str(e)[:120]}"
                r = await loop.run_in_executor(None, _test)
                bypass_debug_results.append(r)
            bypass_debug = " | ".join(bypass_debug_results)
        except Exception as e:
            import traceback
            bypass_debug = f"EXCEPTION: {e} {traceback.format_exc()[-300:]}"

        from utils.youtube_bypass import YouTubeBypass
        bypass = YouTubeBypass()
        result = await bypass.get_audio_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        if result:
            url, info = result
            return web.json_response({
                "ok": True,
                "has_url": True,
                "url_preview": url[:80],
                "title": info.get("title", "?"),
                "duration_text": info.get("duration_text", "?"),
                "manual_cookie_result": manual_cookie_result,
                "yt_direct_test": yt_result,
                "bypass_debug": bypass_debug,
            })
        return web.json_response({
            "ok": False,
            "error": "Bypass returned no result",
            "manual_cookie_result": manual_cookie_result,
            "yt_direct_test": yt_result,
            "bypass_debug": bypass_debug,
            "debug": {
                "cookies_file_exists": cookies_file_ok,
                "cookies_file_content_prefix": cookies_file_content,
                "cookies_env_prefix": env_prefix,
                "cookies_env_length": len(env_val) if env_val else 0,
            }
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
