"""Per-guild music player for Lo Maza."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, TypedDict

import discord
import yt_dlp

from utils.filters import FILTERS, DEFAULT_FILTER

log = logging.getLogger("lo-maza.player")

_FFMPEG_PATH: str | None = None

def _get_ffmpeg() -> str:
    global _FFMPEG_PATH
    if _FFMPEG_PATH is None:
        try:
            import imageio_ffmpeg
            _FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
            log.info("Found ffmpeg via imageio-ffmpeg: %s", _FFMPEG_PATH)
        except Exception as exc:
            log.warning("imageio-ffmpeg not available, falling back to PATH ffmpeg: %s", exc)
            _FFMPEG_PATH = "ffmpeg"
    return _FFMPEG_PATH

# ────────────────────────────────────────────────
# yt-dlp configuration
# ────────────────────────────────────────────────

_COOKIES_FILE = "cookies.txt"

def _try_cookies() -> dict[str, Any]:
    """Write cookies from env var (JSON → Netscape), or use cookies.txt on disk."""
    import os as _os
    import base64 as _b64
    import json as _json

    b64 = _os.environ.get("YOUTUBE_COOKIES_B64")
    if b64:
        try:
            raw = _b64.b64decode(b64).decode("utf-8")
            cookies = _json.loads(raw)
            # Convert JSON cookies → Netscape format
            lines = ["# Netscape HTTP Cookie File"]
            for c in cookies:
                domain = c.get("domain", "")
                if not domain:
                    continue
                flag = "FALSE" if c.get("hostOnly", False) else "TRUE"
                path = c.get("path", "/")
                secure = "TRUE" if c.get("secure", False) else "FALSE"
                expiry = str(int(c.get("expirationDate", 0) or 0))
                name = c.get("name", "")
                value = c.get("value", "")
                lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{value}")
            with open(_COOKIES_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            log.info("Wrote %d cookies (Netscape format) from YOUTUBE_COOKIES_B64", len(cookies))
        except Exception as exc:
            log.warning("Failed to process YOUTUBE_COOKIES_B64: %s", exc)
    if _os.path.isfile(_COOKIES_FILE):
        log.info("Using cookies file: %s (%d bytes)", _COOKIES_FILE, _os.path.getsize(_COOKIES_FILE))
        return {"cookiefile": _COOKIES_FILE}
    return {}

# Shared base options
_BASE_OPTS: dict[str, Any] = {
    "format": "bestaudio/best",
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "source_address": "0.0.0.0",
    "socket_timeout": 30,
    "retries": 5,
    "extractor_retries": 3,
    "fragment_retries": 5,
    "ignore_no_formats_error": True,
    "throttled_rate": "200M",
}

# Extractor args that work for bypass in 2026.
# Android client is the least likely to be blocked by YouTube.
_YOUTUBE_EXTRACTOR_ARGS: dict[str, Any] = {
    "youtube": {
        "player_client": ["android", "web", "ios", "tv_embedded", "tv"],
        "player_skip": ["webpage", "configs"],
        "skip": ["dash", "translated_thumbnails"],
        "include_dash_manifest": False,
        "include_info_json": False,
    },
}

# cookies file (if present on disk)
_COOKIE_OPTS = _try_cookies()

# Search-only options — extract_flat avoids fetching stream URLs,
# which prevents YouTube bot-detection from triggering during search.
YTDL_SEARCH_OPTS: dict[str, Any] = {
    **_BASE_OPTS,
    **_COOKIE_OPTS,
    "default_search": "ytsearch",
    "noplaylist": False,
    "extract_flat": "in_playlist",  # metadata only, no stream URL needed
    "extractor_args": _YOUTUBE_EXTRACTOR_ARGS,
}

# Stream-resolution options.
YTDL_STREAM_OPTS: dict[str, Any] = {
    **_BASE_OPTS,
    **_COOKIE_OPTS,
    "noplaylist": True,
    "extractor_args": _YOUTUBE_EXTRACTOR_ARGS,
}

# Fallback configs if the primary extraction fails (tried in order by resolve_stream_url)
_STREAM_FALLBACK_CONFIGS: list[dict[str, Any]] = [
    # Fallback 1: Android only (most permissive)
    {
        "extractor_args": {
            "youtube": {
                "player_client": ["android"],
                "player_skip": ["webpage", "configs"],
                "skip": ["dash", "translated_thumbnails"],
            }
        },
    },
    # Fallback 2: Web client with cookies simulation
    {
        "extractor_args": {
            "youtube": {
                "player_client": ["web"],
                "player_skip": ["webpage"],
            }
        },
    },
    # Fallback 3: iOS + tv combo
    {
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "tv"],
                "player_skip": ["webpage", "configs"],
                "skip": ["dash", "translated_thumbnails"],
            }
        },
    },
]

FFMPEG_BEFORE_OPTS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"


class Track(TypedDict, total=False):
    title: str
    url: str
    stream_url: str
    duration: float
    thumbnail: str
    artist: str
    requester: str
    requester_avatar: str


# ────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────

def _make_ytdl(opts: dict[str, Any], extra: dict[str, Any] | None = None) -> yt_dlp.YoutubeDL:
    merged = {**opts, **(extra or {})}
    return yt_dlp.YoutubeDL(merged)


def _entry_to_track(entry: dict[str, Any], requester: discord.Member) -> Track:
    """Convert a yt-dlp info dict (flat or full) into a Track."""
    # Flat entries from extract_flat have 'id' and 'title' but no stream url
    video_id = entry.get("id", "")
    page_url = entry.get("webpage_url") or (
        f"https://www.youtube.com/watch?v={video_id}" if video_id else entry.get("url", "")
    )
    # Thumbnail: full entries have 'thumbnail', flat entries need construction
    thumbnail = entry.get("thumbnail") or ""
    if not thumbnail and video_id:
        thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    if not thumbnail:
        thumbnails = entry.get("thumbnails") or []
        thumbnail = thumbnails[-1].get("url", "") if thumbnails else ""

    return Track(
        title=entry.get("title", "Unknown"),
        url=page_url,
        stream_url=entry.get("url", ""),  # empty for flat entries — resolved before playback
        duration=float(entry.get("duration") or 0),
        thumbnail=thumbnail,
        artist=entry.get("uploader") or entry.get("channel") or entry.get("channel_id") or "Unknown",
        requester=str(requester),
        requester_avatar=(
            str(requester.display_avatar.url)
            if hasattr(requester, "display_avatar")
            else ""
        ),
    )


# ────────────────────────────────────────────────
# Search
# ────────────────────────────────────────────────

async def search_tracks(query: str, requester: discord.Member) -> list[Track]:
    """
    Search/resolve tracks.
    For text queries  → extract_flat search (no stream URLs, avoids bot detection).
    For direct URLs   → full extraction with TV client.
    """
    loop = asyncio.get_event_loop()

    def _extract() -> list[Track]:
        is_url = query.startswith(("http://", "https://"))

        if is_url:
            # Direct URL: try primary config, then fallbacks
            configs = [YTDL_STREAM_OPTS] + _STREAM_FALLBACK_CONFIGS
            data = None
            for extra in configs:
                opts = {**YTDL_STREAM_OPTS, **extra}
                try:
                    with _make_ytdl(opts) as ydl:
                        data = ydl.extract_info(query, download=False)
                    if data:
                        break
                except Exception:
                    continue
            if not data:
                return []
        else:
            # Text search: use flat extraction to avoid bot detection
            search_query = f"ytsearch5:{query}"
            with _make_ytdl(YTDL_SEARCH_OPTS) as ydl:
                data = ydl.extract_info(search_query, download=False)

        if not data:
            return []

        entries = data.get("entries") or [data]
        tracks: list[Track] = []
        for entry in entries:
            if entry and entry.get("title"):
                tracks.append(_entry_to_track(entry, requester))
        return tracks

    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _extract), timeout=30)
    except asyncio.TimeoutError:
        log.error("search_tracks timed out for query: %s", query)
        return []
    except Exception as exc:
        log.error("search_tracks error for '%s': %s", query, exc)
        return []


async def resolve_stream_url(track: Track, vc: discord.VoiceClient | None = None) -> str:
    """
    Fetch a fresh, playable stream URL.
    Tries the primary config first, then falls back to alternative configs.
    Sends voice keepalive pings during resolution to prevent timeout.
    """
    loop = asyncio.get_event_loop()
    page_url = track.get("url", "")
    if not page_url:
        return track.get("stream_url", "")

    configs = [YTDL_STREAM_OPTS] + _STREAM_FALLBACK_CONFIGS
    per_config_timeout = 10

    async def _keepalive() -> None:
        """Send voice WS pings so Discord doesn't drop the connection."""
        while True:
            await asyncio.sleep(5)
            try:
                if vc and vc.is_connected():
                    vc.ws.send_heartbeat() if hasattr(vc, 'ws') and hasattr(vc.ws, 'send_heartbeat') else None
            except Exception:
                pass

    keepalive_task = asyncio.create_task(_keepalive()) if vc else None

    try:
        for i, extra in enumerate(configs):
            opts = {**YTDL_STREAM_OPTS, **extra}

            def _resolve(opts=opts) -> str:
                try:
                    with _make_ytdl(opts) as ydl:
                        data = ydl.extract_info(page_url, download=False)
                    if not data:
                        return ""
                    if "entries" in data:
                        data = data["entries"][0] if data["entries"] else {}
                    return data.get("url") or ""
                except Exception as exc:
                    log.debug("Config %d failed for '%s': %s", i, track.get("title"), exc)
                    return ""

            try:
                url = await asyncio.wait_for(loop.run_in_executor(None, _resolve), timeout=per_config_timeout)
                if url:
                    if i > 0:
                        log.info("Resolved '%s' with fallback config %d", track.get("title"), i)
                    return url
            except asyncio.TimeoutError:
                log.warning("Config %d timed out for '%s'", i, track.get("title"))

        log.warning("All configs failed for '%s'", track.get("title"))
        return track.get("stream_url", "")
    finally:
        if keepalive_task:
            keepalive_task.cancel()


async def autocomplete_search(query: str) -> list[str]:
    """Return up to 5 title suggestions for autocomplete (must respond in <2.5 s)."""
    if not query or len(query) < 2:
        return []
    loop = asyncio.get_event_loop()

    def _search() -> list[str]:
        with _make_ytdl(YTDL_SEARCH_OPTS) as ydl:
            data = ydl.extract_info(f"ytsearch5:{query}", download=False)
        if not data or "entries" not in data:
            return []
        return [e.get("title", "") for e in data["entries"] if e and e.get("title")][:5]

    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _search), timeout=2.5)
    except (asyncio.TimeoutError, Exception):
        return []


# ────────────────────────────────────────────────
# FFmpeg audio source builder
# ────────────────────────────────────────────────

def build_audio_source(
    stream_url: str, volume: int = 80, filter_key: str = DEFAULT_FILTER
) -> discord.FFmpegOpusAudio:
    """Create an FFmpegOpusAudio with volume applied via ffmpeg filter."""
    vol = volume / 100
    filter_opts = FILTERS.get(filter_key, FILTERS[DEFAULT_FILTER])["options"]
    af_parts = [f"volume={vol}"]
    if filter_opts:
        af_parts.append(filter_opts)
    ffmpeg_options = f"-vn -af {','.join(af_parts)}"

    return discord.FFmpegOpusAudio(
        stream_url,
        executable=_get_ffmpeg(),
        before_options=FFMPEG_BEFORE_OPTS,
        options=ffmpeg_options,
    )


# ────────────────────────────────────────────────
# Repeat modes
# ────────────────────────────────────────────────
REPEAT_OFF   = 0
REPEAT_SONG  = 1
REPEAT_QUEUE = 2


# ────────────────────────────────────────────────
# Per-guild MusicPlayer
# ────────────────────────────────────────────────

class MusicPlayer:
    """Manages playback state for a single guild."""

    def __init__(
        self,
        guild: discord.Guild,
        voice_client: discord.VoiceClient,
        bot_loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.guild = guild
        self.voice_client = voice_client
        self._loop = bot_loop
        self.queue: list[Track] = []
        self.current_track: Track | None = None
        self.text_channel: discord.TextChannel | None = None
        self.last_error: str | None = None
        self.volume: int = 80
        self.current_filter: str = DEFAULT_FILTER
        self.repeat_mode: int = REPEAT_OFF
        self.now_playing_message: discord.Message | None = None
        self._playing = False
        self._idle_task: asyncio.Task | None = None
        self._started_at: float | None = None
        self._paused_at: float | None = None
        self._progress_task: asyncio.Task | None = None

    # ── Internal helpers ───────────────────────────────────────────────

    def _cancel_idle(self) -> None:
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
            self._idle_task = None

    def _after_play(self, error: Exception | None) -> None:
        if error:
            msg = f"Playback error: {error}"
            log.error(msg)
            self.last_error = msg
        asyncio.run_coroutine_threadsafe(self._advance(), self._loop)

    async def _safe_send(self, text: str) -> None:
        """Send an error message to the guild text channel (if set)."""
        if self.text_channel:
            try:
                from utils.embeds import error_embed
                await self.text_channel.send(embed=error_embed(text))
            except Exception:
                pass

    async def _advance(self) -> None:
        self._playing = False
        self._cancel_idle()
        self._stop_progress_updater()

        if self.repeat_mode == REPEAT_SONG and self.current_track:
            next_track = self.current_track
        elif self.queue:
            if self.repeat_mode == REPEAT_QUEUE and self.current_track:
                self.queue.append(self.current_track)
            next_track = self.queue.pop(0)
            self.current_track = next_track
        else:
            self.current_track = None
            log.info("[%s] Queue empty — will auto-disconnect in 5 min.", self.guild.name)

            async def _idle_disconnect() -> None:
                await asyncio.sleep(300)
                if not self._playing and self.voice_client and self.voice_client.is_connected():
                    log.info("[%s] Auto-disconnecting after idle.", self.guild.name)
                    await self.voice_client.disconnect()

            self._idle_task = self._loop.create_task(_idle_disconnect())
            return

        await self._play_track(next_track)

    async def _play_track(self, track: Track) -> None:
        if not self.voice_client or not self.voice_client.is_connected():
            msg = "VoiceClient disconnected before playback could start."
            log.warning("[%s] %s", self.guild.name, msg)
            self.last_error = msg
            await self._safe_send(msg)
            return

        log.info("[%s] Resolving: %s", self.guild.name, track.get("title"))
        stream_url = await resolve_stream_url(track, self.voice_client)
        track["stream_url"] = stream_url

        if not stream_url:
            msg = "Could not resolve a playable stream URL."
            log.error("[%s] No stream URL for '%s' — skipping.", self.guild.name, track.get("title"))
            self.last_error = msg
            await self._safe_send(f"**{track.get('title')}**: {msg}")
            await self._advance()
            return

        import time
        log.info("[%s] Playing: %s", self.guild.name, track.get("title"))
        source = build_audio_source(stream_url, self.volume, self.current_filter)
        self.current_track = track
        self._started_at = time.time()
        self._paused_at = None
        self._playing = True
        try:
            self.voice_client.play(source, after=self._after_play)
        except Exception as e:
            msg = f"voice_client.play() failed: {e}"
            log.error("[%s] %s", self.guild.name, msg)
            self.last_error = msg
            self._playing = False
            await self._safe_send(msg)
            await self._advance()
            return
        self._start_progress_updater()

    # ── Position tracking ─────────────────────────────────────────────

    def get_position(self) -> float:
        if self._paused_at is not None and self._started_at is not None:
            return self._paused_at - self._started_at
        if self._started_at is not None:
            import time
            return time.time() - self._started_at
        return 0

    def _start_progress_updater(self) -> None:
        self._stop_progress_updater()
        self._progress_task = self._loop.create_task(self._update_progress_loop())

    def _stop_progress_updater(self) -> None:
        if self._progress_task and not self._progress_task.done():
            self._progress_task.cancel()
            self._progress_task = None

    async def _update_progress_loop(self) -> None:
        while True:
            await asyncio.sleep(5)
            if not self.now_playing_message:
                continue
            if not self.is_playing() and not self.is_paused():
                continue
            try:
                from utils.embeds import now_playing_embed
                if self.current_track:
                    embed = now_playing_embed(self, self.current_track)
                    await self.now_playing_message.edit(embed=embed)
            except Exception:
                pass

    # ── Public API ─────────────────────────────────────────────────────

    def enqueue(self, track: Track) -> None:
        self.queue.append(track)

    async def start(self) -> None:
        if self._playing or self.voice_client.is_playing():
            return
        await self._advance()

    def skip(self) -> None:
        if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            self.voice_client.stop()

    def stop(self) -> None:
        self._cancel_idle()
        self._stop_progress_updater()
        self.queue.clear()
        self.current_track = None
        self._playing = False
        self._started_at = None
        self._paused_at = None
        if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            self.voice_client.stop()

    def pause(self) -> None:
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.pause()
            import time
            self._paused_at = time.time()

    def resume(self) -> None:
        if self.voice_client and self.voice_client.is_paused():
            self.voice_client.resume()
            import time
            if self._paused_at is not None and self._started_at is not None:
                paused_duration = time.time() - self._paused_at
                self._started_at += paused_duration
            self._paused_at = None

    def set_volume(self, volume: int) -> None:
        self.volume = max(0, min(200, volume))
        if self.voice_client and self.voice_client.is_playing() and self.current_track:
            stream_url = self.current_track.get("stream_url") or self.current_track.get("url", "")
            if stream_url:
                source = build_audio_source(stream_url, self.volume, self.current_filter)
                self.voice_client.source = source

    def shuffle(self) -> None:
        random.shuffle(self.queue)

    def is_playing(self) -> bool:
        return bool(self.voice_client and self.voice_client.is_playing())

    def is_paused(self) -> bool:
        return bool(self.voice_client and self.voice_client.is_paused())

    async def apply_filter(self, filter_key: str) -> bool:
        self.current_filter = filter_key
        if not self.current_track or not self.voice_client:
            return True
        if not self.voice_client.is_playing() and not self.voice_client.is_paused():
            return True
        stream_url = self.current_track.get("stream_url") or self.current_track.get("url", "")
        if not stream_url:
            return False
        source = build_audio_source(stream_url, self.volume, filter_key)
        self.voice_client.source = source
        return True
