"""Audio filter definitions for Lo Maza Discord music bot."""

from typing import TypedDict


class FilterData(TypedDict):
    name: str
    emoji: str
    options: str
    description: str


# FFmpeg audio filters mapped by key
FILTERS: dict[str, FilterData] = {
    "normal": {
        "name": "Normal",
        "emoji": "🎵",
        "options": "",
        "description": "No filter applied",
    },
    "8d": {
        "name": "8D Audio",
        "emoji": "🎧",
        "options": "apulsator=hz=0.08",
        "description": "Surround sound panning effect",
    },
    "bassboost": {
        "name": "Bass Boost",
        "emoji": "🔊",
        "options": "bass=g=20,dynaudnorm=f=200",
        "description": "Enhanced low-frequency audio",
    },
    "nightcore": {
        "name": "Nightcore",
        "emoji": "🌙",
        "options": "atempo=1.25,asetrate=44100*1.25",
        "description": "Sped-up high-pitched style",
    },
    "vaporwave": {
        "name": "Vaporwave",
        "emoji": "🌊",
        "options": "atempo=0.8,asetrate=44100*0.8",
        "description": "Slowed retro aesthetic",
    },
    "karaoke": {
        "name": "Karaoke",
        "emoji": "🎤",
        "options": "pan=stereo|c0=c0-c1|c1=c1-c0",
        "description": "Vocal reduction mode",
    },
    "tremolo": {
        "name": "Tremolo",
        "emoji": "〰️",
        "options": "tremolo=f=6:d=0.9",
        "description": "Wavering volume effect",
    },
    "vibrato": {
        "name": "Vibrato",
        "emoji": "🎶",
        "options": "vibrato=f=6.5:d=0.5",
        "description": "Pitch wobble effect",
    },
    "rotation": {
        "name": "Rotation",
        "emoji": "🔄",
        "options": "apulsator=hz=0.2",
        "description": "Rotating audio panning",
    },
    "timescale": {
        "name": "Timescale",
        "emoji": "⏩",
        "options": "atempo=1.2",
        "description": "1.2× speed playback",
    },
    "distortion": {
        "name": "Distortion",
        "emoji": "⚡",
        "options": "acrusher=level_in=6:level_out=3:bits=8:mode=log:aa=1",
        "description": "Bit-crushed distorted audio",
    },
}

DEFAULT_FILTER = "normal"
