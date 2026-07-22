"""
YouTubeBypass — bot-detection bypass for YouTube extraction.

Provides async methods:
  get_video_url(query)  → dict | None
  get_audio_url(query)  → (stream_url, info_dict) | None
  search_suggestions(query) → list[str]
"""

from __future__ import annotations

import asyncio
import logging
import os as _os
import base64 as _b64
import json as _json
from typing import Any

import yt_dlp

log = logging.getLogger("lo-maza.youtube_bypass")

_COOKIES_FILE = "cookies.txt"


class YouTubeBypass:
    """Bypass YouTube blocking with multi-strategy yt-dlp extraction."""

    def __init__(self) -> None:
        self._cookies_opts = self._load_cookies()
        self._base_opts: dict[str, Any] = {
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
        self._extractor_args: dict[str, Any] = {
            "youtube": {
                "player_client": ["android", "web", "ios", "tv_embedded", "tv"],
                "player_skip": ["webpage", "configs"],
                "skip": ["dash", "translated_thumbnails"],
                "include_dash_manifest": False,
                "include_info_json": False,
            },
        }
        self._stream_opts: dict[str, Any] = {
            **self._base_opts,
            **self._cookies_opts,
            "default_search": "ytsearch",
            "noplaylist": True,
            "extractor_args": self._extractor_args,
        }
        self._search_opts: dict[str, Any] = {
            **self._base_opts,
            **self._cookies_opts,
            "default_search": "ytsearch",
            "noplaylist": False,
            "extract_flat": "in_playlist",
            "extractor_args": self._extractor_args,
        }
        self._fallback_configs: list[dict[str, Any]] = [
            {"extractor_args": {"youtube": {"player_client": ["android"], "player_skip": ["webpage", "configs"], "skip": ["dash", "translated_thumbnails"]}}},
            {"extractor_args": {"youtube": {"player_client": ["web"], "player_skip": ["webpage"]}}},
            {"extractor_args": {"youtube": {"player_client": ["ios", "tv"], "player_skip": ["webpage", "configs"], "skip": ["dash", "translated_thumbnails"]}}},
        ]

    # ── Cookies ────────────────────────────────────────────────────────

    @staticmethod
    def _load_cookies() -> dict[str, Any]:
        b64 = _os.environ.get("YOUTUBE_COOKIES_B64")
        if b64:
            try:
                raw = _b64.b64decode(b64).decode("utf-8")
                cookies = _json.loads(raw)
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
                log.info("Wrote %d cookies (Netscape format)", len(cookies))
            except Exception as exc:
                log.warning("Failed to process YOUTUBE_COOKIES_B64: %s", exc)
        if _os.path.isfile(_COOKIES_FILE):
            log.info("Using cookies: %s (%d bytes)", _COOKIES_FILE, _os.path.getsize(_COOKIES_FILE))
            return {"cookiefile": _COOKIES_FILE}
        return {}

    # ── yt-dlp helpers ─────────────────────────────────────────────────

    @staticmethod
    def _make_ydl(opts: dict[str, Any]) -> yt_dlp.YoutubeDL:
        return yt_dlp.YoutubeDL(opts)

    def _extract(self, url: str, opts: dict[str, Any]) -> dict[str, Any] | None:
        try:
            with self._make_ydl(opts) as ydl:
                data = ydl.extract_info(url, download=False)
            return data
        except Exception as exc:
            log.debug("Extraction failed for %s: %s", url, exc)
            return None

    # ── Build video info dict from yt-dlp data ─────────────────────────

    @staticmethod
    def _build_info(data: dict[str, Any]) -> dict[str, Any]:
        if "entries" in data:
            data = data["entries"][0] if data["entries"] else {}
        if not data:
            return {}
        video_id = data.get("id", "")
        dur = data.get("duration") or 0
        mins, secs = divmod(int(dur), 60)
        hours, mins = divmod(mins, 60)
        dur_text = f"{hours}:{mins:02d}:{secs:02d}" if hours else f"{mins}:{secs:02d}"
        thumbnail = data.get("thumbnail") or ""
        if not thumbnail and video_id:
            thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        return {
            "title": data.get("title", "Unknown"),
            "url": data.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
            "channel": data.get("uploader") or data.get("channel") or "Unknown",
            "duration": dur,
            "duration_text": dur_text,
            "thumbnail": thumbnail,
            "id": video_id,
        }

    # ── Public API ─────────────────────────────────────────────────────

    async def get_video_url(self, query: str) -> dict[str, Any] | None:
        """
        Search YouTube or resolve a URL.
        Returns: {title, url, channel, duration, duration_text, thumbnail, id}
        or None if no results.
        """
        is_url = query.startswith(("http://", "https://"))
        loop = asyncio.get_event_loop()

        def _search() -> dict[str, Any] | None:
            if is_url:
                configs = [self._stream_opts] + self._fallback_configs
                data = None
                for extra in configs:
                    opts = {**self._stream_opts, **extra}
                    data = self._extract(query, opts)
                    if data:
                        break
            else:
                # Full extraction (not flat) so we get duration
                data = self._extract(f"ytsearch1:{query}", self._stream_opts)
            if not data:
                return None
            info = self._build_info(data)
            return info if info.get("title") else None

        try:
            return await asyncio.wait_for(loop.run_in_executor(None, _search), timeout=25)
        except asyncio.TimeoutError:
            log.error("get_video_url timed out for: %s", query)
            return None
        except Exception as exc:
            log.error("get_video_url error for '%s': %s", query, exc)
            return None

    async def get_audio_url(self, query_or_url: str) -> tuple[str, dict[str, Any]] | None:
        """
        Get a playable audio stream URL + full info.
        Returns (stream_url, info_dict) or None.
        """
        loop = asyncio.get_event_loop()

        def _resolve() -> tuple[str, dict[str, Any]] | None:
            # First get page URL from query
            is_url = query_or_url.startswith(("http://", "https://"))
            page_url = query_or_url

            if not is_url:
                # Search first
                data = self._extract(f"ytsearch1:{query_or_url}", self._search_opts)
                if not data:
                    return None
                if "entries" in data and data["entries"]:
                    data = data["entries"][0]
                page_url = data.get("webpage_url") or (
                    f"https://www.youtube.com/watch?v={data.get('id', '')}" if data.get("id") else None
                )
                if not page_url:
                    return None

            # Resolve stream URL with fallbacks
            configs = [self._stream_opts] + self._fallback_configs
            for extra in configs:
                opts = {**self._stream_opts, **extra}
                data = self._extract(page_url, opts)
                if data:
                    if "entries" in data:
                        data = data["entries"][0] if data["entries"] else {}
                    stream_url = data.get("url") or ""
                    if stream_url:
                        info = self._build_info(data)
                        return stream_url, info
            return None

        try:
            return await asyncio.wait_for(loop.run_in_executor(None, _resolve), timeout=35)
        except asyncio.TimeoutError:
            log.error("get_audio_url timed out for: %s", query_or_url)
            return None
        except Exception as exc:
            log.error("get_audio_url error for '%s': %s", query_or_url, exc)
            return None

    async def search_suggestions(self, query: str) -> list[str]:
        """Autocomplete (max 5 titles, must respond in <2.5s)."""
        if not query or len(query) < 2:
            return []
        loop = asyncio.get_event_loop()

        def _search() -> list[str]:
            data = self._extract(f"ytsearch5:{query}", self._search_opts)
            if not data or "entries" not in data:
                return []
            return [e.get("title", "") for e in data["entries"] if e and e.get("title")][:5]

        try:
            return await asyncio.wait_for(loop.run_in_executor(None, _search), timeout=2.5)
        except (asyncio.TimeoutError, Exception):
            return []
