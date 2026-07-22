"""Embed builders for Lo Maza Discord music bot."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import discord

from utils.filters import FILTERS

if TYPE_CHECKING:
    from utils.player import Track, MusicPlayer

# ── Brand colours ──────────────────────────────────────────────────────────────
COLOR         = discord.Color(0xA855F7)
ERROR_COLOR   = discord.Color(0xED4245)
SUCCESS_COLOR = discord.Color(0x57F287)
INFO_COLOR    = discord.Color(0x5865F2)
WARN_COLOR    = discord.Color(0xFEE75C)

REPEAT_LABELS = {0: "Off", 1: "🔂 Song", 2: "🔁 Queue"}

WAVE = "▁▂▃▄▅▆▇█▇▆▅▄▃▂▁"
DIVIDER = "━━━━━━━━━━━━━━━━━━━━━━"


def progress_bar(position: float, duration: float, length: int = 16) -> str:
    if duration <= 0:
        return "▬" * length
    filled = max(0, min(length - 1, int((position / duration) * length)))
    return "▬" * filled + "🔘" + "▬" * (length - filled - 1)


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def vol_bar(volume: int, length: int = 10) -> str:
    filled = round(volume / (200 / length))
    filled = max(0, min(length, filled))
    return "█" * filled + "░" * (length - filled)


# ── Now Playing ────────────────────────────────────────────────────────────────

def now_playing_embed(player: "MusicPlayer", track: "Track") -> discord.Embed:
    filter_data = FILTERS.get(player.current_filter, FILTERS["normal"])
    repeat_label = REPEAT_LABELS.get(player.repeat_mode, "Off")

    title_text = track.get("title", "Unknown Track")
    url        = track.get("url", "")
    artist     = track.get("artist", "Unknown Artist")
    duration   = track.get("duration", 0)

    status_icon = "▶️" if player.is_playing() else ("⏸️" if player.is_paused() else "⏹️")
    position = player.get_position() if hasattr(player, 'get_position') else 0

    bar = progress_bar(position, duration)
    pos_str = format_duration(position)
    dur_str = format_duration(duration)

    embed = discord.Embed(
        description=(
            f"### {status_icon}  **Now Playing**\n"
            f"# [{title_text}]({url})\n"
            f"-# by **{artist}**\n"
            f"`{pos_str}` {bar} `{dur_str}`\n"
            f"-# {DIVIDER}"
        ),
        color=COLOR,
    )

    if track.get("thumbnail"):
        embed.set_image(url=track["thumbnail"])

    embed.add_field(
        name="🔊 Volume",
        value=f"`{player.volume}%` {vol_bar(player.volume)}",
        inline=True,
    )
    embed.add_field(
        name="🎚️ Filter",
        value=f"{filter_data['emoji']} {filter_data['name']}",
        inline=True,
    )
    embed.add_field(
        name="🔁 Repeat",
        value=repeat_label,
        inline=True,
    )
    embed.add_field(
        name="📋 Queue",
        value=f"**{len(player.queue)}** track{'s' if len(player.queue) != 1 else ''}",
        inline=True,
    )

    if track.get("requester"):
        embed.set_footer(
            text=f"Requested by {track['requester']}",
            icon_url=track.get("requester_avatar") or None,
        )
    return embed


# ── Queue ──────────────────────────────────────────────────────────────────────

def queue_embed(player: "MusicPlayer", page: int = 1) -> discord.Embed:
    per_page    = 10
    queue       = player.queue
    total_pages = max(1, math.ceil(len(queue) / per_page))
    page        = max(1, min(page, total_pages))

    embed = discord.Embed(title="📋  Queue", color=COLOR)
    parts: list[str] = []

    current = player.current_track
    if current:
        dur = format_duration(current.get("duration", 0))
        status = "▶️" if player.is_playing() else "⏸️"
        parts.append(
            f"### {status} **Now Playing**\n"
            f"╰ [{current['title']}]({current['url']}) `{dur}`\n"
        )

    if not queue:
        parts.append("*Queue is empty — use `/play` to add tracks.*")
    else:
        parts.append(f"### **Up Next** `({len(queue)} tracks)`")
        start = (page - 1) * per_page
        for i, t in enumerate(queue[start : start + per_page], start=start + 1):
            dur = format_duration(t.get("duration", 0))
            parts.append(f"`{i:>2}.` [{t['title']}]({t['url']}) `{dur}`")

    embed.description = "\n".join(parts)

    total_dur    = sum(t.get("duration", 0) for t in queue)
    repeat_label = REPEAT_LABELS.get(player.repeat_mode, "Off")
    embed.set_footer(
        text=f"Page {page}/{total_pages} · {len(queue)} tracks · {format_duration(total_dur)} total · Repeat: {repeat_label}"
    )
    if current and current.get("thumbnail"):
        embed.set_thumbnail(url=current["thumbnail"])
    return embed


# ── Utility embeds ─────────────────────────────────────────────────────────────

def error_embed(message: str) -> discord.Embed:
    return discord.Embed(description=f"### ❌  {message}", color=ERROR_COLOR)


def success_embed(message: str) -> discord.Embed:
    return discord.Embed(description=f"### ✅  {message}", color=SUCCESS_COLOR)


def info_embed(title: str, message: str) -> discord.Embed:
    return discord.Embed(title=title, description=message, color=INFO_COLOR)


def warn_embed(message: str) -> discord.Embed:
    return discord.Embed(description=f"### ⚠️  {message}", color=WARN_COLOR)


# ── Help ───────────────────────────────────────────────────────────────────────

def help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎵  Lo Maza — Premium Music Bot",
        description=(
            f"`{WAVE}`\n"
            "> Your premium music experience for Discord.\n"
            "> Supports **YouTube**, **SoundCloud**, and direct URLs.\n\u200b"
        ),
        color=COLOR,
    )
    embed.add_field(
        name="🎧  Playback",
        value=(
            "`/play <query>` — Search & queue a song or playlist\n"
            "`/nowplaying` — Current track + interactive controls\n"
            "`/skip` — Skip to next track\n"
            "`/queue` — Browse & manage the queue"
        ),
        inline=False,
    )
    embed.add_field(
        name="🔊  Audio",
        value=(
            "`/volume <0–200>` — Set playback volume\n"
            "`/filter` — Apply audio effects (8D, Bass Boost, Nightcore...)\n"
            "`/repeat <Off/Song/Queue>` — Set repeat mode\n"
            "`/shuffle` — Randomise the queue"
        ),
        inline=False,
    )
    embed.add_field(
        name="📡  Connection",
        value=(
            "`/join` — Pull bot into your voice channel\n"
            "`/leave` — Disconnect & clear queue"
        ),
        inline=False,
    )
    embed.add_field(
        name="💡  Tips",
        value=(
            "> Paste a **playlist URL** to queue all tracks at once\n"
            "> Use the **buttons** on `/nowplaying` to control playback live\n"
            "> Hit **🔁** on `/nowplaying` to cycle Off -> Song -> Queue repeat"
        ),
        inline=False,
    )
    embed.set_footer(text="Lo Maza · Premium Music Platform · Made with ❤️")
    return embed


# ── Track added (rich card) ────────────────────────────────────────────────────

def added_embed(track: "Track", queue_position: int) -> discord.Embed:
    embed = discord.Embed(
        description=(
            f"### ✅  **Added to Queue**\n"
            f"**[{track.get('title', 'Unknown')}]({track.get('url', '')})**\n"
            f"-# by {track.get('artist', 'Unknown Artist')}"
        ),
        color=SUCCESS_COLOR,
    )
    if track.get("thumbnail"):
        embed.set_thumbnail(url=track["thumbnail"])
    dur = format_duration(track.get("duration", 0))
    embed.add_field(name="⏱️ Duration", value=f"`{dur}`", inline=True)
    embed.add_field(name="📋 Position", value=f"`#{queue_position}`", inline=True)
    if track.get("requester"):
        embed.set_footer(
            text=f"Requested by {track['requester']}",
            icon_url=track.get("requester_avatar") or None,
        )
    return embed


def playlist_added_embed(count: int, requester: str) -> discord.Embed:
    embed = discord.Embed(
        description=f"### ✅  **Playlist Added**\nQueued **{count} tracks** from the playlist.",
        color=SUCCESS_COLOR,
    )
    embed.set_footer(text=f"Requested by {requester}")
    return embed
