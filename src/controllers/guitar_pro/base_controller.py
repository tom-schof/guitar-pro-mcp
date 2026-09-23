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
    Song, Track, Measure, MeasureHeader, Voice, Beat, Note, 
    Duration, TimeSignature, KeySignature, TripletFeel
)

from guitarpro import parse

class GuitarProMixin:
    """Mixin class providing basic Guitar Pro functionality."""
    
    def __init__(self):
        """Initialize the Guitar Pro controller."""
        self.current_song = None
        
    def _ensure_song_loaded(self):
        """Ensure a song is loaded before performing operations."""
        if not self.current_song:
            raise ValueError("No song is currently loaded") 