import os
import sys
from typing import Dict, List, Optional, Union, Any

# Use the pip-installed pyguitarpro dependency; fall back to a vendored
# PyGuitarPro checkout at the repo root if one exists.
repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
guitar_pro_path = os.path.join(repo_root, 'PyGuitarPro')
if os.path.exists(guitar_pro_path) and guitar_pro_path not in sys.path:
    sys.path.insert(0, guitar_pro_path)

import guitarpro as gp

from guitarpro.models import (
    Song, Track, Measure, MeasureHeader, Voice, Beat, BeatStatus, Note,
    Duration, TimeSignature, KeySignature, TripletFeel
)

from guitarpro import parse

PERCUSSION_CHANNEL = 9

class GuitarProMixin:
    """Mixin class providing basic Guitar Pro functionality."""
    
    def __init__(self):
        """Initialize the Guitar Pro controller."""
        self.current_song = None
        
    def _ensure_song_loaded(self):
        """Ensure a song is loaded before performing operations."""
        if not self.current_song:
            raise ValueError("No song is currently loaded")

    def _free_channels(self, source: Optional[Track] = None):
        """Pick unused MIDI (channel, effectChannel) for a new track."""
        if source is not None and source.isPercussionTrack:
            return PERCUSSION_CHANNEL, PERCUSSION_CHANNEL
        used = set()
        for t in self.current_song.tracks:
            used.update({t.channel.channel, t.channel.effectChannel})
        free = [c for c in range(16) if c != PERCUSSION_CHANNEL and c not in used]
        if len(free) >= 2:
            return free[0], free[1]
        if free:
            return free[0], free[0]
        # All 15 melodic channels in use: share the source's channels.
        if source is not None:
            return source.channel.channel, source.channel.effectChannel
        return 0, 1

    @staticmethod
    def _fill_rests(measure: Measure) -> None:
        """Give an empty measure one rest per time-signature beat in voice 0.

        New measures otherwise have no beats, which leaves nothing for
        add_notes to address.
        """
        voice = measure.voices[0]
        if voice.beats:
            return
        ts = measure.header.timeSignature
        for _ in range(ts.numerator):
            voice.beats.append(Beat(voice, duration=Duration(value=ts.denominator.value),
                                    status=BeatStatus.rest))
