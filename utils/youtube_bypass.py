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


class YouTubeBypass:
    def __init__(self) -> None:
        self._write_cookies()

    @staticmethod
    def _write_cookies() -> None:
        b64_raw = _os.environ.get("YOUTUBE_COOKIES_B64")
        if not b64_raw or _os.path.isfile(_COOKIES_FILE):
            return
        b64 = b64_raw.strip()

        def _decode(s: str) -> str | None:
            for s2 in [s, s + "=" * (4 - len(s) % 4 if len(s) % 4 else 0),
                       s.replace("-", "+").replace("_", "/")]:
                try:
                    return _b64.b64decode(s2).decode("utf-8")
                except Exception:
                    continue
            return None

        raw = _decode(b64) or b64
        if not raw:
            return

        # JSON array → Netscape
        try:
            cookies = _json.loads(raw)
            if isinstance(cookies, list):
                lines = ["# Netscape HTTP Cookie File"]
                for c in cookies:
                    d = c.get("domain", "")
                    if not d:
                        continue
                    flag = "FALSE" if c.get("hostOnly", False) else "TRUE"
                    p = c.get("path", "/")
                    sec = "TRUE" if c.get("secure", False) else "FALSE"
                    exp = str(int(c.get("expirationDate", 0) or 0))
                    n = c.get("name", "")
                    v = c.get("value", "")
                    lines.append(f"{d}\t{flag}\t{p}\t{sec}\t{exp}\t{n}\t{v}")
                Path(_COOKIES_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
                log.info("Wrote %d cookies", len(cookies))
                return
        except Exception:
            pass
        # Raw Netscape
        try:
            if raw.strip().startswith("#"):
                Path(_COOKIES_FILE).write_text(raw, encoding="utf-8")
                log.info("Wrote raw cookies (%d bytes)", len(raw))
        except Exception:
            pass

    @staticmethod
    def _opts() -> dict[str, Any]:
        return {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "cookiefile": _COOKIES_FILE if _os.path.isfile(_COOKIES_FILE) else None,
            "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"},
        }

    @staticmethod
    def _run(opts: dict[str, Any], url: str) -> dict[str, Any] | None:
        opts = {k: v for k, v in opts.items() if v is not None}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as exc:
            log.debug("yt-dlp failed for %s: %s", url, exc)
            return None

    async def get_video_url(self, query: str) -> dict[str, Any] | None:
        loop = asyncio.get_event_loop()
        opts = self._opts()
        q = query if query.startswith(("http://", "https://")) else f"ytsearch1:{query}"

        def _search() -> dict[str, Any] | None:
            data = self._run(opts, q)
            if not data:
                return None
            if "entries" in data:
                entries = data.get("entries") or []
                data = entries[0] if entries else {}
            if not data or not data.get("title"):
                return None
            vid = data.get("id", "")
            dur = data.get("duration") or 0
            m, s = divmod(int(dur), 60)
            h, m = divmod(m, 60)
            return {
                "title": data.get("title", "Unknown"),
                "url": data.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}",
                "channel": data.get("uploader") or data.get("channel") or "Unknown",
                "duration": dur,
                "duration_text": f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}",
                "thumbnail": data.get("thumbnail") or (f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else ""),
                "id": vid,
            }

        try:
            return await asyncio.wait_for(loop.run_in_executor(None, _search), timeout=30)
        except (asyncio.TimeoutError, Exception):
            return None

    async def get_audio_url(self, query_or_url: str) -> tuple[str, dict[str, Any]] | None:
        loop = asyncio.get_event_loop()
        opts = self._opts()

        def _resolve() -> tuple[str, dict[str, Any]] | None:
            q = query_or_url if query_or_url.startswith(("http://", "https://")) else f"ytsearch1:{query_or_url}"
            data = self._run(opts, q)
            if not data:
                return None
            if "entries" in data:
                entries = data.get("entries") or []
                data = entries[0] if entries else {}
            if not data:
                return None
            url = data.get("url", "")
            if not url:
                fmts = data.get("formats", [])
                if fmts:
                    for f in reversed(fmts):
                        if f.get("url"):
                            url = f["url"]
                            break
            if not url:
                req = data.get("requested_formats", [])
                for f in req:
                    if f.get("url"):
                        url = f["url"]
                        break
            if not url:
                return None
            vid = data.get("id", "")
            dur = data.get("duration") or 0
            m, s = divmod(int(dur), 60)
            h, m = divmod(m, 60)
            info = {
                "title": data.get("title", "Unknown"),
                "url": data.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}",
                "channel": data.get("uploader") or data.get("channel") or "Unknown",
                "duration": dur,
                "duration_text": f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}",
                "thumbnail": data.get("thumbnail") or (f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else ""),
                "id": vid,
            }
            return url, info

        try:
            return await asyncio.wait_for(loop.run_in_executor(None, _resolve), timeout=40)
        except (asyncio.TimeoutError, Exception):
            return None

    async def search_suggestions(self, query: str) -> list[str]:
        if not query or len(query) < 2:
            return []
        loop = asyncio.get_event_loop()
        opts = self._opts()
        opts["extract_flat"] = "in_playlist"

        def _search() -> list[str]:
            data = self._run(opts, f"ytsearch5:{query}")
            if not data or "entries" not in data:
                return []
            return [e.get("title", "") for e in data["entries"] if e and e.get("title")][:5]

        try:
            return await asyncio.wait_for(loop.run_in_executor(None, _search), timeout=3.0)
        except Exception:
            return []
