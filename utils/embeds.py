from __future__ import annotations

import math
from typing import TYPE_CHECKING

import discord

from utils.filters import FILTERS

if TYPE_CHECKING:
    from utils.player import Track, MusicPlayer

# ── Brand colours ──
COLOR         = discord.Color(0xA855F7)
ERROR_COLOR   = discord.Color(0xED4245)
SUCCESS_COLOR = discord.Color(0x57F287)
INFO_COLOR    = discord.Color(0x5865F2)
WARN_COLOR    = discord.Color(0xFEE75C)

REPEAT_LABELS = {0: "Off", 1: "Song", 2: "Queue"}

WAVE = "▁▂▃▄▅▆▇█▇▆▅▄▃▂▁"
EQ   = "▰▰▰▰▰▰▰"

_field_style = "╰ "

def progress_bar(position: float, duration: float, length: int = 14) -> str:
    if duration <= 0:
        return "─" * length
    filled = max(0, min(length - 1, int((position / duration) * length)))
    return "━" * filled + "●" + "─" * (length - filled - 1)

def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def _duration_display(duration: float) -> str:
    if duration <= 0:
        return "🔴 Live"
    return format_duration(duration)


def now_playing_embed(player: MusicPlayer, track: Track) -> discord.Embed:
    filter_data = FILTERS.get(player.current_filter, FILTERS["normal"])
    repeat_label = REPEAT_LABELS.get(player.repeat_mode, "Off")

    title  = track.get("title", "Unknown Track")
    url    = track.get("url", "")
    artist = track.get("artist", "Unknown Artist")
    dur    = track.get("duration", 0)

    if player.is_playing():
        status = f"▶️ **NOW PLAYING**  `{EQ}`"
    elif player.is_paused():
        status = "⏸️ **PAUSED**"
    else:
        status = "⏹️ **STOPPED**"

    position = player.get_position() if hasattr(player, 'get_position') else 0
    bar = progress_bar(position, dur)
    pos_str = format_duration(position)
    dur_str = _duration_display(dur)

    desc = (
        f"### {status}\n"
        f"# [{title}]({url})\n"
        f"-# {_field_style}{artist}\n"
        f"\n"
        f"`{pos_str}` {bar} `{dur_str}`\n"
        f"\n"
    )

    embed = discord.Embed(description=desc, color=COLOR)

    if track.get("thumbnail"):
        embed.set_image(url=track["thumbnail"])

    vol_filled = round(player.volume / 20)
    vol_bar_chars = "█" * vol_filled + "░" * (10 - vol_filled)
    embed.add_field(name="🔊 Volume", value=f"`{vol_bar_chars}` **{player.volume}%**", inline=True)
    embed.add_field(name="🎚️ Filter", value=f"{filter_data['emoji']} **{filter_data['name']}**", inline=True)
    embed.add_field(name="🔁 Repeat", value=f"**{repeat_label}**", inline=True)

    qlen = len(player.queue)
    embed.add_field(name="📋 Queue", value=f"**{qlen}** track{'s' if qlen != 1 else ''}", inline=True)

    if track.get("requester"):
        embed.set_footer(
            text=f"Requested by {track['requester']}",
            icon_url=track.get("requester_avatar") or None,
        )
    return embed


def queue_embed(player: MusicPlayer, page: int = 1) -> discord.Embed:
    per_page    = 10
    queue       = player.queue
    total_pages = max(1, math.ceil(len(queue) / per_page))
    page        = max(1, min(page, total_pages))

    embed = discord.Embed(title=f"📋 Queue  `({len(queue)} tracks)`", color=COLOR)
    parts: list[str] = []

    current = player.current_track
    if current:
        dur = _duration_display(current.get("duration", 0))
        icon = "▶️" if player.is_playing() else "⏸️"
        parts.append(
            f"### {icon} **Now Playing**\n"
            f"{_field_style}[{current['title']}]({current['url']}) `{dur}`\n"
        )

    if not queue:
        parts.append("*Queue is empty — use `/play` to add tracks.*")
    else:
        parts.append(f"### **Up Next**")
        start = (page - 1) * per_page
        for i, t in enumerate(queue[start : start + per_page], start=start + 1):
            dur = _duration_display(t.get("duration", 0))
            parts.append(f"`{i:>2}.` [{t['title']}]({t['url']}) `{dur}`")

    embed.description = "\n".join(parts)

    total_dur = sum(t.get("duration", 0) for t in queue if t.get("duration", 0) > 0)
    footer = f"Page {page}/{total_pages} · {len(queue)} tracks"
    if total_dur > 0:
        footer += f" · {format_duration(total_dur)} total"
    footer += f" · Repeat: {REPEAT_LABELS.get(player.repeat_mode, 'Off')}"
    embed.set_footer(text=footer)

    if current and current.get("thumbnail"):
        embed.set_thumbnail(url=current["thumbnail"])
    return embed


def error_embed(message: str) -> discord.Embed:
    return discord.Embed(description=f"### ❌  {message}", color=ERROR_COLOR)

def success_embed(message: str) -> discord.Embed:
    return discord.Embed(description=f"### ✅  {message}", color=SUCCESS_COLOR)

def info_embed(title: str, message: str) -> discord.Embed:
    return discord.Embed(title=title, description=message, color=INFO_COLOR)

def warn_embed(message: str) -> discord.Embed:
    return discord.Embed(description=f"### ⚠️  {message}", color=WARN_COLOR)


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
            "> Hit **🔁** on `/nowplaying` to cycle Off → Song → Queue repeat"
        ),
        inline=False,
    )
    embed.set_footer(text="Lo Maza · Premium Music Platform · Made with ❤️")
    return embed


def added_embed(track: Track, queue_position: int) -> discord.Embed:
    dur = _duration_display(track.get("duration", 0))
    embed = discord.Embed(
        description=(
            f"### ✅  **Added to Queue**\n"
            f"**[{track.get('title', 'Unknown')}]({track.get('url', '')})**\n"
            f"-# {_field_style}by {track.get('artist', 'Unknown Artist')}"
        ),
        color=SUCCESS_COLOR,
    )
    if track.get("thumbnail"):
        embed.set_thumbnail(url=track["thumbnail"])
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
