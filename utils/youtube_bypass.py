"""
YouTubeBypass — bot-detection bypass for YouTube extraction 2025/2026.

Uses PO Token (Proof of Origin) provider plugins + multi-client fallback chain.

Provides async methods:
  get_video_url(query)   → dict | None
  get_audio_url(query)   → (stream_url, info_dict) | None
  search_suggestions(query) → list[str]
"""

from __future__ import annotations

import asyncio
import logging
import os as _os
import base64 as _b64
import json as _json
from pathlib import Path
from typing import Any

import yt_dlp

log = logging.getLogger("lo-maza.youtube_bypass")

_COOKIES_FILE = "cookies.txt"
_POTOKEN_FILE = "potoken.txt"


class YouTubeBypass:
    """Bypass YouTube blocking with multi-strategy yt-dlp extraction."""

    def __init__(self) -> None:
        self._cookies_opts = self._load_cookies()
        self._potoken_opts = self._load_potoken()
        self._visitor_data = self._load_visitor_data()

        # ── Base yt-dlp options ────────────────────────────────────────
        self._base_opts: dict[str, Any] = {
            "format": "bestaudio/best",
            "nocheckcertificate": True,
            "ignoreerrors": False,
            "logtostderr": False,
            "quiet": True,
            "no_warnings": True,
            "source_address": "0.0.0.0",
            "socket_timeout": 30,
            "retries": 10,
            "extractor_retries": 5,
            "fragment_retries": 10,
            "ignore_no_formats_error": True,
            "throttled_rate": "200M",
            # Rate-limit to avoid IP bans
            "sleep_interval_requests": 1.0,
            "sleep_interval": 0.5,
            # User-agent rotation
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
            },
        }

        # ── Extractor args with PO Token + visitor_data ────────────────
        self._extractor_args: dict[str, Any] = {
            "youtube": {
                "player_client": ["android", "web", "ios", "tv"],
                "player_skip": ["webpage", "configs"],
                "skip": ["dash", "translated_thumbnails", "hls"],
                "include_dash_manifest": False,
                "include_info_json": False,
                "lang": ["en"],
            },
        }

        # Inject PO Token into extractor args if available
        pot = self._potoken_opts.get("po_token")
        if pot:
            self._extractor_args["youtube"]["po_token"] = pot
            log.info("PO Token loaded from env/ file")

        # Inject visitor_data if available
        vd = self._visitor_data
        if vd:
            self._extractor_args["youtube"]["visitor_data"] = vd
            log.info("Visitor data loaded from env/ file")

        # ── Stream resolution options ──────────────────────────────────
        self._stream_opts: dict[str, Any] = {
            **self._base_opts,
            **self._cookies_opts,
            "default_search": "ytsearch",
            "noplaylist": True,
            "extractor_args": self._extractor_args,
        }

        # ── Search options (flat for speed) ────────────────────────────
        self._search_opts: dict[str, Any] = {
            **self._base_opts,
            **self._cookies_opts,
            "default_search": "ytsearch",
            "noplaylist": False,
            "extract_flat": "in_playlist",
            "extractor_args": self._extractor_args,
        }

        # ── Fallback configs (different clients + skip levels) ─────────
        self._fallback_configs: list[dict[str, Any]] = [
            # 1. Android only (most reliable for audio)
            {
                "extractor_args": {
                    "youtube": {
                        "player_client": ["android"],
                        "player_skip": ["webpage", "configs"],
                        "skip": ["dash", "translated_thumbnails", "hls"],
                    }
                },
                "format": "bestaudio/best",
            },
            # 2. Web client with full extraction
            {
                "extractor_args": {
                    "youtube": {
                        "player_client": ["web"],
                        "player_skip": ["webpage"],
                        "skip": [],
                    }
                },
                "format": "bestaudio/best",
            },
            # 3. iOS + TV combined
            {
                "extractor_args": {
                    "youtube": {
                        "player_client": ["ios", "tv"],
                        "player_skip": ["webpage", "configs"],
                        "skip": ["dash", "translated_thumbnails"],
                    }
                },
                "format": "bestaudio/best",
            },
            # 4. TV embedded (last resort)
            {
                "extractor_args": {
                    "youtube": {
                        "player_client": ["tv_embedded"],
                        "player_skip": ["webpage", "configs"],
                        "skip": ["dash", "hls"],
                    }
                },
                "format": "worstaudio/worst",
            },
        ]

        # ── Inject PO Token + visitor_data into all fallback configs ──
        if pot or vd:
            for fb in self._fallback_configs:
                ya = fb.setdefault("extractor_args", {}).setdefault("youtube", {})
                if pot:
                    ya.setdefault("po_token", pot)
                if vd:
                    ya.setdefault("visitor_data", vd)

    # ── PO Token loading ───────────────────────────────────────────────

    @staticmethod
    def _load_potoken() -> dict[str, str]:
        """Load PO Token from env var YOUTUBE_POTOKEN or potoken.txt file."""
        pot = _os.environ.get("YOUTUBE_POTOKEN")
        if pot:
            log.info("Loaded PO Token from YOUTUBE_POTOKEN env var")
            return {"po_token": pot}

        pot_file = Path(_POTOKEN_FILE)
        if pot_file.is_file():
            try:
                pot = pot_file.read_text("utf-8").strip()
                if pot:
                    log.info("Loaded PO Token from %s", _POTOKEN_FILE)
                    return {"po_token": pot}
            except Exception as exc:
                log.warning("Failed to read %s: %s", _POTOKEN_FILE, exc)

        return {}

    # ── Visitor data loading ───────────────────────────────────────────

    @staticmethod
    def _load_visitor_data() -> str:
        """Load visitor_data from env var YOUTUBE_VISITOR_DATA."""
        vd = _os.environ.get("YOUTUBE_VISITOR_DATA")
        if vd:
            log.info("Loaded visitor_data from YOUTUBE_VISITOR_DATA env var")
            return vd
        return ""

    # ── Cookies (Netscape format) ──────────────────────────────────────

    @staticmethod
    def _load_cookies() -> dict[str, Any]:
        """Load cookies from YOUTUBE_COOKIES_B64 or cookies.txt.
        Tries multiple formats: base64 JSON → raw JSON → Netscape text.
        """
        b64_raw = _os.environ.get("YOUTUBE_COOKIES_B64")
        # If cookies.txt already exists, just use it
        if _os.path.isfile(_COOKIES_FILE):
            log.info("Using existing cookie file: %s (%d bytes)", _COOKIES_FILE, _os.path.getsize(_COOKIES_FILE))
            return {"cookiefile": _COOKIES_FILE}

        if not b64_raw:
            return {}

        # Strip whitespace/newlines that Render may inject
        b64 = b64_raw.strip()

        def _b64decode_padded(s: str) -> str | None:
            """Safe base64 decode with auto-padding."""
            try:
                return _b64.b64decode(s).decode("utf-8")
            except Exception:
                pass
            # Try with padding
            try:
                missing = 4 - len(s) % 4
                if missing != 4:
                    s += "=" * missing
                return _b64.b64decode(s).decode("utf-8")
            except Exception:
                pass
            # Try URL-safe variant
            try:
                s2 = s.replace("-", "+").replace("_", "/")
                missing = 4 - len(s2) % 4
                if missing != 4:
                    s2 += "=" * missing
                return _b64.b64decode(s2).decode("utf-8")
            except Exception:
                pass
            return None

        raw = _b64decode_padded(b64)

        # If base64 decode failed, treat env var as plaintext
        if raw is None:
            raw = b64

        if not raw:
            log.warning("YOUTUBE_COOKIES_B64 set but empty after decode")
            return {}

        # Try JSON array → Netscape format
        try:
            cookies = _json.loads(raw)
            if isinstance(cookies, list):
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
                log.info("Wrote %d cookies (Netscape format) from JSON env var", len(cookies))
                return {"cookiefile": _COOKIES_FILE}
        except Exception:
            pass

        # Try raw Netscape format
        try:
            if raw.strip().startswith("#"):
                with open(_COOKIES_FILE, "w", encoding="utf-8") as f:
                    f.write(raw)
                log.info("Wrote raw Netscape cookies file (%d bytes)", len(raw))
                return {"cookiefile": _COOKIES_FILE}
        except Exception:
            pass

        log.warning("YOUTUBE_COOKIES_B64 set but could not parse (len=%d, prefix=%s)",
                     len(b64), b64[:80])
        return {}

    # ── yt-dlp helpers ─────────────────────────────────────────────────

    @staticmethod
    def _make_ydl(opts: dict[str, Any]) -> yt_dlp.YoutubeDL:
        return yt_dlp.YoutubeDL(opts)

    def _extract(self, url: str, opts: dict[str, Any]) -> dict[str, Any] | None:
        """Run yt-dlp extract_info with error logging."""
        try:
            with self._make_ydl(opts) as ydl:
                data = ydl.extract_info(url, download=False)
            return data
        except yt_dlp.utils.DownloadError as exc:
            err_str = str(exc)
            if "HTTP Error 403" in err_str:
                log.warning("403 Forbidden for %s — trying fallback config", url)
            elif "HTTP Error 404" in err_str:
                log.warning("404 Not Found for %s", url)
            elif "Sign in" in err_str or "confirm your age" in err_str.lower():
                log.warning("Age-restricted / Sign-in required for %s", url)
            else:
                log.debug("Extraction failed for %s: %s", url, exc)
            return None
        except Exception as exc:
            log.debug("Extraction failed for %s: %s", url, exc)
            return None

    @staticmethod
    def _last_stream_url(data: dict[str, Any]) -> str:
        """Extract the best stream URL from yt-dlp result."""
        if not data:
            return ""
        # Prefer url over requested_formats
        url = data.get("url", "")
        if url:
            return url
        # Check formats array for an audio stream
        formats = data.get("formats", [])
        if formats:
            # Pick the last audio format (usually best quality)
            for f in reversed(formats):
                furl = f.get("url", "")
                if furl:
                    return furl
        # Check requested_formats fallback
        req_fmts = data.get("requested_formats", [])
        for f in req_fmts:
            furl = f.get("url", "")
            if furl:
                return furl
        return ""

    # ── Build video info dict from yt-dlp data ─────────────────────────

    @staticmethod
    def _build_info(data: dict[str, Any]) -> dict[str, Any]:
        if "entries" in data:
            entries = data.get("entries") or []
            data = entries[0] if entries else {}
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
                for extra in configs:
                    opts = {**self._stream_opts, **extra}
                    data = self._extract(query, opts)
                    if data:
                        info = self._build_info(data)
                        if info.get("title"):
                            return info
            else:
                # Full extraction so we get duration / thumbnail
                data = self._extract(f"ytsearch1:{query}", self._stream_opts)
                if data:
                    info = self._build_info(data)
                    if info.get("title"):
                        return info
            return None

        try:
            return await asyncio.wait_for(loop.run_in_executor(None, _search), timeout=30)
        except asyncio.TimeoutError:
            log.error("get_video_url timed out for: %s", query)
            return None
        except Exception as exc:
            log.error("get_video_url error for '%s': %s", query, exc)
            return None

    async def get_audio_url(
        self, query_or_url: str
    ) -> tuple[str, dict[str, Any]] | None:
        """
        Get a playable audio stream URL + full info.

        Uses PO Token (if available) + multi-client fallback chain.

        Returns (stream_url, info_dict) or None.
        """
        loop = asyncio.get_event_loop()

        def _resolve() -> tuple[str, dict[str, Any]] | None:
            is_url = query_or_url.startswith(("http://", "https://"))
            page_url = query_or_url

            if not is_url:
                # Search → get page URL first
                data = self._extract(
                    f"ytsearch1:{query_or_url}", self._search_opts
                )
                if not data:
                    return None
                if "entries" in data and data["entries"]:
                    data = data["entries"][0]
                page_url = data.get("webpage_url") or (
                    f"https://www.youtube.com/watch?v={data.get('id', '')}"
                    if data.get("id")
                    else None
                )
                if not page_url:
                    log.warning("Could not resolve page URL for query: %s", query_or_url)
                    return None

            # Resolve stream URL with full fallback chain
            configs: list[dict[str, Any]] = [self._stream_opts] + self._fallback_configs
            for idx, extra in enumerate(configs):
                opts = {**self._stream_opts, **extra}
                log.debug(
                    "Trying config %d/%d for %s (client: %s)",
                    idx + 1,
                    len(configs),
                    page_url,
                    opts.get("extractor_args", {})
                    .get("youtube", {})
                    .get("player_client", "?"),
                )
                data = self._extract(page_url, opts)
                if data:
                    if "entries" in data:
                        data = data["entries"][0] if data["entries"] else {}
                    stream_url = self._last_stream_url(data)
                    if stream_url:
                        info = self._build_info(data)
                        log.info(
                            "Stream resolved for '%s' via config %d (client: %s)",
                            info.get("title", "?"),
                            idx + 1,
                            opts.get("extractor_args", {})
                            .get("youtube", {})
                            .get("player_client", "?"),
                        )
                        return stream_url, info
                log.debug("Config %d failed — trying next...", idx + 1)

            log.error("All %d configs failed for %s", len(configs), page_url)
            return None

        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _resolve), timeout=40
            )
        except asyncio.TimeoutError:
            log.error("get_audio_url timed out for: %s", query_or_url)
            return None
        except Exception as exc:
            log.error("get_audio_url error for '%s': %s", query_or_url, exc)
            return None

    async def search_suggestions(self, query: str) -> list[str]:
        """Autocomplete (max 5 titles, must respond in <3s)."""
        if not query or len(query) < 2:
            return []
        loop = asyncio.get_event_loop()

        def _search() -> list[str]:
            data = self._extract(f"ytsearch5:{query}", self._search_opts)
            if not data or "entries" not in data:
                return []
            return [
                e.get("title", "")
                for e in data["entries"]
                if e and e.get("title")
            ][:5]

        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _search), timeout=3.0
            )
        except (asyncio.TimeoutError, Exception):
            return []
