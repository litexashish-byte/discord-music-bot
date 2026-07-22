"""Music commands cog for Lo Maza."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

log = logging.getLogger("lo-maza.cogs.music")

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import (
    added_embed,
    error_embed,
    info_embed,
    now_playing_embed,
    playlist_added_embed,
    queue_embed,
    success_embed,
    warn_embed,
)
from utils.filters import FILTERS, DEFAULT_FILTER
from utils.player import MusicPlayer, search_tracks, autocomplete_search, REPEAT_OFF, REPEAT_SONG, REPEAT_QUEUE
from utils.youtube_bypass import YouTubeBypass

if TYPE_CHECKING:
    from main import LoMaza


# ── Player store (guild_id → MusicPlayer) ─────────────────────────────────────
players: dict[int, MusicPlayer] = {}

# ── Safe response helper ───────────────────────────────────────────────────────
async def safe_respond(
    interaction: discord.Interaction,
    embed: discord.Embed,
    ephemeral: bool = True,
    view: discord.ui.View | None = None,
) -> None:
    """Send a response safely, handling both deferred and fresh interactions."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=ephemeral, view=view)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=ephemeral, view=view)
    except Exception as e:
        log.error("Failed to respond to interaction %s: %s", interaction.id, e)


def get_player(guild: discord.Guild, vc: discord.VoiceClient, loop: asyncio.AbstractEventLoop) -> MusicPlayer:
    if guild.id not in players:
        players[guild.id] = MusicPlayer(guild, vc, loop)
    else:
        players[guild.id].voice_client = vc
    return players[guild.id]


def remove_player(guild_id: int) -> None:
    players.pop(guild_id, None)


# ── UI Components ─────────────────────────────────────────────────────────────

class FilterSelect(discord.ui.Select):
    def __init__(self, player: MusicPlayer) -> None:
        self.player = player
        options = [
            discord.SelectOption(
                label=data["name"],
                value=key,
                emoji=data["emoji"],
                description=data["description"],
                default=(key == player.current_filter),
            )
            for key, data in FILTERS.items()
        ]
        super().__init__(
            placeholder="🎚️ Choose an audio effect…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        chosen = self.values[0]
        applied = await self.player.apply_filter(chosen)
        filter_data = FILTERS[chosen]
        if applied:
            await interaction.response.send_message(
                embed=success_embed(
                    f"Filter set to {filter_data['emoji']} **{filter_data['name']}** — {filter_data['description']}"
                ),
                ephemeral=True,
            )
        else:
            self.player.current_filter = chosen
            await interaction.response.send_message(
                embed=info_embed(
                    "Filter Saved",
                    f"{filter_data['emoji']} **{filter_data['name']}** will apply on the next track.",
                ),
                ephemeral=True,
            )


class FilterView(discord.ui.View):
    def __init__(self, player: MusicPlayer) -> None:
        super().__init__(timeout=60)
        self.add_item(FilterSelect(player))


_REPEAT_LABELS = {
    REPEAT_OFF:   ("🔁", "Repeat: Off",   discord.ButtonStyle.secondary),
    REPEAT_SONG:  ("🔂", "Repeat: Song",  discord.ButtonStyle.success),
    REPEAT_QUEUE: ("🔁", "Repeat: Queue", discord.ButtonStyle.primary),
}


class PlayerButtons(discord.ui.View):
    """Interactive control panel shown with the Now Playing embed."""

    def __init__(self, player: MusicPlayer) -> None:
        super().__init__(timeout=None)
        self.player = player
        self._update_repeat_button()

    def _update_repeat_button(self) -> None:
        """Sync the repeat button label/style to the current repeat mode."""
        btn = discord.utils.get(self.children, custom_id="repeat_btn")
        if btn and isinstance(btn, discord.ui.Button):
            emoji, label, style = _REPEAT_LABELS[self.player.repeat_mode]
            btn.emoji = emoji
            btn.label = label
            btn.style = style

    async def _refresh_embed(self, interaction: discord.Interaction) -> None:
        """Redraw the Now Playing embed after a state change."""
        if self.player.current_track:
            self._update_repeat_button()
            embed = now_playing_embed(self.player, self.player.current_track)
            await interaction.message.edit(embed=embed, view=self)

    # ── Row 1: transport ───────────────────────────────────────────────

    @discord.ui.button(emoji="⏮️", label="Restart", style=discord.ButtonStyle.secondary, row=0)
    async def restart_track(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Restart the current track from the beginning."""
        if not self.player.current_track:
            await interaction.response.defer()
            return
        track = self.player.current_track
        # Re-enqueue at front and skip (which pops next)
        self.player.queue.insert(0, track)
        self.player.skip()
        await interaction.response.send_message(
            embed=success_embed("⏮️ Restarting track…"), ephemeral=True, delete_after=3
        )

    @discord.ui.button(emoji="⏸️", label="Pause", style=discord.ButtonStyle.primary, row=0)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.player.is_paused():
            self.player.resume()
            button.label = "Pause"
            button.emoji = "⏸️"
            button.style = discord.ButtonStyle.primary
        elif self.player.is_playing():
            self.player.pause()
            button.label = "Resume"
            button.emoji = "▶️"
            button.style = discord.ButtonStyle.success
        await interaction.response.defer()
        await self._refresh_embed(interaction)

    @discord.ui.button(emoji="⏭️", label="Skip", style=discord.ButtonStyle.secondary, row=0)
    async def skip(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.player.skip()
        await interaction.response.send_message(
            embed=success_embed("⏭️ Skipped to next track!"), ephemeral=True, delete_after=3
        )

    @discord.ui.button(emoji="⏹️", label="Stop", style=discord.ButtonStyle.danger, row=0)
    async def stop(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.player.stop()
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        await interaction.message.edit(view=self)
        await interaction.response.send_message(
            embed=success_embed("⏹️ Stopped and queue cleared."), ephemeral=True, delete_after=5
        )

    # ── Row 2: extras ──────────────────────────────────────────────────

    @discord.ui.button(emoji="🔉", label="Vol −10", style=discord.ButtonStyle.secondary, row=1)
    async def vol_down(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.player.set_volume(self.player.volume - 10)
        await interaction.response.defer()
        await self._refresh_embed(interaction)

    @discord.ui.button(emoji="🔊", label="Vol +10", style=discord.ButtonStyle.secondary, row=1)
    async def vol_up(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.player.set_volume(self.player.volume + 10)
        await interaction.response.defer()
        await self._refresh_embed(interaction)

    @discord.ui.button(
        emoji="🔁", label="Repeat: Off", style=discord.ButtonStyle.secondary,
        custom_id="repeat_btn", row=1
    )
    async def repeat_toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.player.repeat_mode = (self.player.repeat_mode + 1) % 3
        await interaction.response.defer()
        await self._refresh_embed(interaction)

    @discord.ui.button(emoji="🎚️", label="Effects", style=discord.ButtonStyle.secondary, row=1)
    async def filter_btn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        view = FilterView(self.player)
        await interaction.response.send_message(
            embed=info_embed(
                "🎚️ Audio Effects",
                "Choose an effect to apply instantly — no restart needed:",
            ),
            view=view,
            ephemeral=True,
        )


class QueuePaginator(discord.ui.View):
    def __init__(self, player: MusicPlayer) -> None:
        super().__init__(timeout=120)
        self.player = player
        self.page = 1

    @discord.ui.button(emoji="⬅️", label="Prev", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page = max(1, self.page - 1)
        await interaction.response.edit_message(embed=queue_embed(self.player, self.page), view=self)

    @discord.ui.button(emoji="➡️", label="Next", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page += 1
        await interaction.response.edit_message(embed=queue_embed(self.player, self.page), view=self)

    @discord.ui.button(emoji="🔀", label="Shuffle", style=discord.ButtonStyle.primary)
    async def shuffle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.player.queue:
            self.player.shuffle()
        await interaction.response.edit_message(embed=queue_embed(self.player, 1), view=self)

    @discord.ui.button(emoji="🗑️", label="Clear Queue", style=discord.ButtonStyle.danger)
    async def clear_queue(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.player.queue.clear()
        await interaction.response.edit_message(embed=queue_embed(self.player, 1), view=self)


# ── Checks ────────────────────────────────────────────────────────────────────

def user_in_voice(interaction: discord.Interaction) -> bool:
    return (
        isinstance(interaction.user, discord.Member)
        and interaction.user.voice is not None
        and interaction.user.voice.channel is not None
    )


# ── Music Cog ─────────────────────────────────────────────────────────────────

class Music(commands.Cog):
    def __init__(self, bot: "LoMaza") -> None:
        self.bot = bot

    # ── Voice helpers ──────────────────────────────────────────────────

    async def _ensure_voice(self, interaction: discord.Interaction) -> discord.VoiceClient | None:
        if not user_in_voice(interaction):
            await interaction.followup.send(
                embed=error_embed("You need to be in a voice channel first."), ephemeral=True
            )
            return None

        member = interaction.user
        assert isinstance(member, discord.Member)
        channel = member.voice.channel
        guild = interaction.guild
        assert guild is not None

        vc = guild.voice_client
        if vc and isinstance(vc, discord.VoiceClient):
            if vc.channel != channel:
                try:
                    await vc.move_to(channel)
                except Exception as e:
                    log.error("Failed to move to channel: %s", e)
                    await interaction.followup.send(
                        embed=error_embed("Could not move to your voice channel."), ephemeral=True
                    )
                    return None
            return vc
        try:
            return await asyncio.wait_for(channel.connect(timeout=10.0), timeout=15.0)
        except asyncio.TimeoutError:
            log.error("Voice connection timed out for %s", guild.name)
            await interaction.followup.send(
                embed=error_embed("Voice connection timed out. Try again."), ephemeral=True
            )
            return None
        except Exception as e:
            log.error("Voice connection failed: %s", e)
            await interaction.followup.send(
                embed=error_embed(f"Could not connect to voice: {e}"), ephemeral=True
            )
            return None

    # ── Commands ───────────────────────────────────────────────────────

    @app_commands.command(name="play", description="Search and play music from YouTube or SoundCloud")
    @app_commands.describe(query="Song name, artist, or paste a URL / playlist link")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        try:
            await interaction.response.defer()

            vc = await self._ensure_voice(interaction)
            if not vc:
                return

            if not interaction.guild:
                await safe_respond(interaction, error_embed("Guild not found."))
                return

            player = get_player(interaction.guild, vc, self.bot.loop)
            if interaction.channel and isinstance(interaction.channel, discord.TextChannel):
                player.text_channel = interaction.channel

            await interaction.followup.send(
                embed=info_embed("🔍 Searching…", f"Looking up `{query}`"), ephemeral=True
            )

            tracks = await search_tracks(query, interaction.user)
            if not tracks:
                await interaction.followup.send(
                    embed=error_embed("No results found. Try a different search term or URL."),
                    ephemeral=True,
                )
                return

            was_idle = not player.is_playing() and not player.is_paused()

            if query.startswith(("http://", "https://")) and len(tracks) > 1:
                for t in tracks:
                    player.enqueue(t)
                requester_name = str(interaction.user)
                await interaction.followup.send(
                    embed=playlist_added_embed(len(tracks), requester_name), ephemeral=True
                )
            else:
                t = tracks[0]
                player.enqueue(t)
                queue_pos = len(player.queue)
                await interaction.followup.send(
                    embed=added_embed(t, queue_pos), ephemeral=True
                )

            if was_idle:
                await player.start()
        except Exception as e:
            log.error("Error in /play: %s", e, exc_info=True)
            await safe_respond(interaction, error_embed("Something went wrong. Try again."))

    @play.autocomplete("query")
    async def play_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        suggestions = await autocomplete_search(current)
        return [app_commands.Choice(name=s[:100], value=s[:100]) for s in suggestions]

    @app_commands.command(name="nowplaying", description="Show the currently playing track with controls")
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        try:
            if not interaction.guild:
                await safe_respond(interaction, error_embed("Guild not found."))
                return
            player = players.get(interaction.guild.id)

            if not player or not player.current_track:
                await safe_respond(
                    interaction,
                    error_embed("Nothing is playing right now. Use `/play` to start."),
                    ephemeral=True,
                )
                return

            embed = now_playing_embed(player, player.current_track)
            view = PlayerButtons(player)
            await interaction.response.send_message(embed=embed, view=view)
            player.now_playing_message = await interaction.original_response()
        except Exception as e:
            log.error("Error in /nowplaying: %s", e, exc_info=True)
            await safe_respond(interaction, error_embed("Something went wrong."))

    @app_commands.command(name="queue", description="View the upcoming track queue")
    async def queue_cmd(self, interaction: discord.Interaction) -> None:
        try:
            if not interaction.guild:
                await safe_respond(interaction, error_embed("Guild not found."))
                return
            player = players.get(interaction.guild.id)

            if not player:
                await safe_respond(
                    interaction,
                    error_embed("Nothing is playing. Use `/play` to start."),
                    ephemeral=True,
                )
                return

            view = QueuePaginator(player)
            await interaction.response.send_message(embed=queue_embed(player, 1), view=view)
        except Exception as e:
            log.error("Error in /queue: %s", e, exc_info=True)
            await safe_respond(interaction, error_embed("Something went wrong."))

    @app_commands.command(name="skip", description="Skip the current track")
    async def skip(self, interaction: discord.Interaction) -> None:
        try:
            if not interaction.guild:
                await safe_respond(interaction, error_embed("Guild not found."))
                return
            player = players.get(interaction.guild.id)

            if not player or not player.is_playing():
                await safe_respond(interaction, error_embed("Nothing to skip."), ephemeral=True)
                return

            player.skip()
            await interaction.response.send_message(embed=success_embed("⏭️ Skipped!"))
        except Exception as e:
            log.error("Error in /skip: %s", e, exc_info=True)
            await safe_respond(interaction, error_embed("Something went wrong."))

    @app_commands.command(name="volume", description="Set the playback volume (0–200)")
    @app_commands.describe(level="Volume level from 0 to 200")
    async def volume(self, interaction: discord.Interaction, level: app_commands.Range[int, 0, 200]) -> None:
        try:
            if not interaction.guild:
                await safe_respond(interaction, error_embed("Guild not found."))
                return
            player = players.get(interaction.guild.id)

            if not player:
                await safe_respond(interaction, error_embed("Nothing is playing."), ephemeral=True)
                return

            player.set_volume(level)
            filled = level // 10
            bar = "█" * filled + "░" * (20 - filled)
            await interaction.response.send_message(
                embed=success_embed(f"🔊 Volume set to **{level}%**\n`{bar}`")
            )
        except Exception as e:
            log.error("Error in /volume: %s", e, exc_info=True)
            await safe_respond(interaction, error_embed("Something went wrong."))

    @app_commands.command(name="filter", description="Apply an audio effect to the current track")
    async def filter_cmd(self, interaction: discord.Interaction) -> None:
        try:
            if not interaction.guild:
                await safe_respond(interaction, error_embed("Guild not found."))
                return
            player = players.get(interaction.guild.id)

            if not player:
                await safe_respond(interaction, error_embed("Nothing is playing."), ephemeral=True)
                return

            view = FilterView(player)
            await interaction.response.send_message(
                embed=info_embed(
                    "🎚️ Audio Effects",
                    "Pick an effect below — it applies instantly with no restart.",
                ),
                view=view,
                ephemeral=True,
            )
        except Exception as e:
            log.error("Error in /filter: %s", e, exc_info=True)
            await safe_respond(interaction, error_embed("Something went wrong."))

    @app_commands.command(name="join", description="Pull Lo Maza into your voice channel")
    async def join(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.defer(ephemeral=True)
            vc = await self._ensure_voice(interaction)
            if vc:
                await interaction.followup.send(
                    embed=success_embed(f"Joined **{vc.channel}** 🎵"), ephemeral=True
                )
        except Exception as e:
            log.error("Error in /join: %s", e, exc_info=True)
            await safe_respond(interaction, error_embed("Could not join voice channel."))

    @app_commands.command(name="leave", description="Disconnect from the voice channel and clear the queue")
    async def leave(self, interaction: discord.Interaction) -> None:
        try:
            if not interaction.guild:
                await safe_respond(interaction, error_embed("Guild not found."))
                return
            player = players.get(interaction.guild.id)

            if player:
                player.stop()
                remove_player(interaction.guild.id)

            vc = interaction.guild.voice_client
            if vc and isinstance(vc, discord.VoiceClient):
                await vc.disconnect()

            await interaction.response.send_message(embed=success_embed("👋 Disconnected and queue cleared."))
        except Exception as e:
            log.error("Error in /leave: %s", e, exc_info=True)
            await safe_respond(interaction, error_embed("Could not disconnect."))

    @app_commands.command(name="shuffle", description="Shuffle the current queue")
    async def shuffle_cmd(self, interaction: discord.Interaction) -> None:
        try:
            if not interaction.guild:
                await safe_respond(interaction, error_embed("Guild not found."))
                return
            player = players.get(interaction.guild.id)

            if not player or not player.queue:
                await safe_respond(
                    interaction,
                    warn_embed("The queue is empty — nothing to shuffle."),
                    ephemeral=True,
                )
                return

            player.shuffle()
            await interaction.response.send_message(
                embed=success_embed(f"🔀 Shuffled **{len(player.queue)}** tracks in the queue!")
            )
        except Exception as e:
            log.error("Error in /shuffle: %s", e, exc_info=True)
            await safe_respond(interaction, error_embed("Something went wrong."))

    @app_commands.command(name="repeat", description="Set repeat mode: Off / Song / Queue")
    @app_commands.describe(mode="Choose what to repeat")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Off",   value=0),
        app_commands.Choice(name="Song",  value=1),
        app_commands.Choice(name="Queue", value=2),
    ])
    async def repeat_cmd(self, interaction: discord.Interaction, mode: int) -> None:
        try:
            if not interaction.guild:
                await safe_respond(interaction, error_embed("Guild not found."))
                return
            player = players.get(interaction.guild.id)

            if not player:
                await safe_respond(interaction, error_embed("Nothing is playing."), ephemeral=True)
                return

            player.repeat_mode = mode
            labels = {0: "🔁 Repeat **Off**", 1: "🔂 Repeating **this song**", 2: "🔁 Repeating the **queue**"}
            await interaction.response.send_message(embed=success_embed(labels[mode]))
        except Exception as e:
            log.error("Error in /repeat: %s", e, exc_info=True)
            await safe_respond(interaction, error_embed("Something went wrong."))

    @app_commands.command(name="help", description="Show all Lo Maza commands")
    async def help_cmd(self, interaction: discord.Interaction) -> None:
        try:
            from utils.embeds import help_embed
            await interaction.response.send_message(embed=help_embed(), ephemeral=True)
        except Exception as e:
            log.error("Error in /help: %s", e, exc_info=True)
            await safe_respond(interaction, error_embed("Something went wrong."))

    @app_commands.command(name="testbypass", description="Test YouTube bypass for a query/URL")
    @app_commands.describe(query="Song name, artist, or paste a URL")
    async def testbypass(self, interaction: discord.Interaction, query: str) -> None:
        try:
            await interaction.response.defer(ephemeral=True)
            bypass = YouTubeBypass()
            info = await bypass.get_video_url(query)
            if not info:
                await interaction.followup.send(
                    embed=error_embed("Bypass failed — no results found."), ephemeral=True
                )
                return
            from utils.embeds import SUCCESS_COLOR
            embed = discord.Embed(
                title=info.get("title", "Unknown"),
                description=f"Channel: {info.get('channel', 'Unknown')}",
                color=SUCCESS_COLOR,
            )
            embed.add_field(name="⏱️ Duration", value=info.get("duration_text", "0:00"), inline=True)
            embed.add_field(name="🔗 URL", value=info.get("url", "N/A"), inline=False)
            if info.get("thumbnail"):
                embed.set_thumbnail(url=info["thumbnail"])
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            log.error("Error in /testbypass: %s", e, exc_info=True)
            await safe_respond(interaction, error_embed("Something went wrong."))

    # ── Event listeners ────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Auto-disconnect when the bot is left alone in a voice channel."""
        guild = member.guild
        vc = guild.voice_client
        if not vc or not isinstance(vc, discord.VoiceClient):
            return
        if vc.channel and len([m for m in vc.channel.members if not m.bot]) == 0:
            await asyncio.sleep(60)
            # Re-check after the grace period
            if vc.channel and len([m for m in vc.channel.members if not m.bot]) == 0:
                player = players.get(guild.id)
                if player:
                    player.stop()
                    remove_player(guild.id)
                await vc.disconnect()


async def setup(bot: "LoMaza") -> None:
    await bot.add_cog(Music(bot))
