"""Per-guild music player for Lo Maza."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, TypedDict

import discord

from utils.filters import FILTERS, DEFAULT_FILTER
from utils.youtube_bypass import YouTubeBypass

log = logging.getLogger("lo-maza.player")

# Global YouTubeBypass instance (initialized once)
_bypass: YouTubeBypass | None = None

def get_bypass() -> YouTubeBypass:
    global _bypass
    if _bypass is None:
        _bypass = YouTubeBypass()
    return _bypass

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

FFMPEG_BEFORE_OPTS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"

def _build_ffmpeg_before(cookies_file: str = "cookies.txt") -> str:
    """Return before_options with reconnect + browser User-Agent + cookies."""
    import os as _os
    cmd = f'{FFMPEG_BEFORE_OPTS} -user_agent "{UA}"'
    if _os.path.isfile(cookies_file):
        try:
            with open(cookies_file) as f:
                pairs = []
                for line in f:
                    if line.startswith("#") or not line.strip():
                        continue
                    cols = line.strip().split("\t")
                    if len(cols) >= 7:
                        pairs.append(f"{cols[5]}={cols[6]}")
            if pairs:
                cookie_val = "; ".join(pairs)
                cmd += f' -headers "Cookie: {cookie_val}"'
        except Exception:
            pass
    return cmd


class Track(TypedDict, total=False):
    title: str
    url: str
    stream_url: str
    duration: float
    thumbnail: str
    artist: str
    requester: str
    requester_avatar: str


def _entry_to_track(info: dict[str, Any], requester: discord.Member) -> Track:
    return Track(
        title=info.get("title", "Unknown"),
        url=info.get("url", ""),
        stream_url=info.get("stream_url", ""),
        duration=float(info.get("duration") or 0),
        thumbnail=info.get("thumbnail", ""),
        artist=info.get("channel", info.get("artist", "Unknown")),
        requester=str(requester),
        requester_avatar=(
            str(requester.display_avatar.url)
            if hasattr(requester, "display_avatar")
            else ""
        ),
    )


async def search_tracks(query: str, requester: discord.Member) -> list[Track]:
    """Search tracks using YouTubeBypass."""
    bypass = get_bypass()
    info = await bypass.get_video_url(query)
    if not info:
        return []
    info["stream_url"] = info.get("stream_url", "")
    return [_entry_to_track(info, requester)]


async def resolve_stream_url(track: Track, vc: discord.VoiceClient | None = None) -> str:
    """Resolve a playable stream URL using YouTubeBypass."""
    bypass = get_bypass()
    page_url = track.get("url", "")
    if not page_url:
        return track.get("stream_url", "")
    result = await bypass.get_audio_url(page_url)
    if result:
        stream_url, info = result
        return stream_url
    return track.get("stream_url", "")


async def autocomplete_search(query: str) -> list[str]:
    """Autocomplete using YouTubeBypass."""
    return await get_bypass().search_suggestions(query)


# ────────────────────────────────────────────────
# FFmpeg audio source builder
# ────────────────────────────────────────────────

def build_audio_source(
    stream_url: str, volume: int = 80, filter_key: str = DEFAULT_FILTER
) -> discord.FFmpegOpusAudio:
    """Create an FFmpegOpusAudio source (no system opus needed)."""
    vol = volume / 100
    filter_opts = FILTERS.get(filter_key, FILTERS[DEFAULT_FILTER])["options"]
    af_parts = [f"volume={vol}"]
    if filter_opts:
        af_parts.append(filter_opts)
    ffmpeg_options = f"-vn -af {','.join(af_parts)}"

    return discord.FFmpegOpusAudio(
        stream_url,
        executable=_get_ffmpeg(),
        before_options=_build_ffmpeg_before(),
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
            asyncio.run_coroutine_threadsafe(self._safe_send(msg), self._loop)
        asyncio.run_coroutine_threadsafe(self._advance(), self._loop)

    async def _safe_send(self, text: str) -> None:
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

        if not self.voice_client or not self.voice_client.is_connected():
            msg = "VoiceClient disconnected during URL resolution."
            log.warning("[%s] %s", self.guild.name, msg)
            self.last_error = msg
            await self._safe_send(msg)
            return

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

        # Check if playback actually started (ffmpeg might fail silently)
        await asyncio.sleep(2)
        if not self.voice_client or not self.voice_client.is_playing():
            msg = "Playback did not start — ffmpeg may have failed to connect."
            log.warning("[%s] %s", self.guild.name, msg)
            self.last_error = msg
            self._playing = False
            await self._safe_send(msg)
            self.voice_client.stop()
            await self._advance()

    # ── Position tracking ─────────────────────────────────────────────

    def get_position(self) -> float:
        if self._paused_at is not None and self._started_at is not None:
            return self._paused_at - self._started_at
        if self._started_at is not None:
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
        if self._playing or (self.voice_client and self.voice_client.is_playing()):
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
            self._paused_at = time.time()

    def resume(self) -> None:
        if self.voice_client and self.voice_client.is_paused():
            self.voice_client.resume()
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
