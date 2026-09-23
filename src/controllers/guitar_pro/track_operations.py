from typing import Dict, List, Any, Optional
from .base_controller import GuitarProMixin
import guitarpro as gp
from guitarpro.models import Song, Track, Measure, Voice, NoteType
import logging

logger = logging.getLogger(__name__)

class TrackOperationsController(GuitarProMixin):
    """Controller for Guitar Pro track operations."""
    
    def get_tracks(self) -> List[Dict[str, Any]]:
        """
        Get a list of tracks in the song.
        
        Returns:
            list: List of track information
        """
        if self.current_song is None:
            return []
            
        return [
            {
                "name": track.name,
                "index": i,
                "strings": len(track.strings),
                "instrument": track.channel.instrument,
                "is_percussion": track.isPercussionTrack
            }
            for i, track in enumerate(self.current_song.tracks)
        ]
        
    def get_track_notes(self, track_index: int, start_measure: int = 0,
                        end_measure: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get the notes from a specific track.
        
        Args:
            track_index (int): Index of the track
            start_measure (int): First measure to include (0-based)
            end_measure (int, optional): Last measure to include (inclusive); defaults to the end
            
        Returns:
            list: List of notes with their properties
        """
        if self.current_song is None:
            logger.warning("No song loaded")
            return []
            
        if track_index < 0 or track_index >= len(self.current_song.tracks):
            logger.warning(f"Invalid track index: {track_index}")
            return []
            
        track = self.current_song.tracks[track_index]
        last = len(track.measures) - 1 if end_measure is None else min(end_measure, len(track.measures) - 1)
        notes = []
        
        for measure_index in range(max(start_measure, 0), last + 1):
            measure = track.measures[measure_index]
            for voice_index, voice in enumerate(measure.voices):
                for beat_index, beat in enumerate(voice.beats):
                    for note in beat.notes:
                        notes.append({
                            "measure": measure_index,
                            "voice": voice_index,
                            "beat": beat_index,
                            "string": note.string,
                            "value": note.value,
                            "pitch": note.realValue,
                            "duration": beat.duration.value,
                            "is_dotted": beat.duration.isDotted,
                            "type": note.type.name,
                            "has_tie": note.type == NoteType.tie
                        })
        
        return notes
        
    def add_track(self, name: str) -> int:
        """
        Add a new track to the song.
        
        Args:
            name (str): Name of the track
            
        Returns:
            int: Index of the new track
        """
        if self.current_song is None:
            # Avoid recursive loop by creating a minimal song
            self.current_song = Song()
            self.current_song.title = "New Song"
            
        # Track() already has standard 6-string tuning (E A D G B E) and one
        # measure per song measure header.
        track = Track(self.current_song, number=len(self.current_song.tracks) + 1)
        track.name = name
        track.channel.instrument = 24  # Acoustic Guitar
        track.channel.channel, track.channel.effectChannel = self._free_channels()
        for measure in track.measures:
            self._fill_rests(measure)
            
        self.current_song.tracks.append(track)
        return len(self.current_song.tracks) - 1
        
    def set_track_properties(self, track_index: int, name: str = None,
                           instrument: int = None, volume: int = None,
                           pan: int = None) -> bool:
        """
        Set properties of a track.
        
        Args:
            track_index (int): Index of the track
            name (str, optional): Name of the track
            instrument (int, optional): MIDI instrument number (0-127)
            volume (int, optional): Volume (0-100)
            pan (int, optional): Pan (-64 to 63)
            
        Returns:
            bool: True if successful, False otherwise
        """
        if self.current_song is None:
            print("No song loaded")
            return False
            
        if track_index < 0 or track_index >= len(self.current_song.tracks):
            print(f"Invalid track index: {track_index}")
            return False
            
        try:
            track = self.current_song.tracks[track_index]
            
            if name is not None:
                track.name = name
                
            if instrument is not None:
                if 0 <= instrument <= 127:
                    track.channel.instrument = instrument
                    # The RSE sound and amp preset belong to the old instrument
                    # (e.g. an overdrive preset would stay on a clean guitar).
                    track.rse.instrument.instrument = instrument
                    track.rse.instrument.effect = ''
                    track.rse.instrument.effectCategory = ''
                else:
                    print("Instrument must be between 0 and 127")
                    return False
                    
            if volume is not None:
                if 0 <= volume <= 100:
                    track.channel.volume = volume
                else:
                    print("Volume must be between 0 and 100")
                    return False
                    
            if pan is not None:
                if -64 <= pan <= 63:
                    track.channel.balance = pan
                else:
                    print("Pan must be between -64 and 63")
                    return False
                    
            return True
        except Exception as e:
            print(f"Error setting track properties: {e}")
            return False
            
    def transpose_track(self, track_index: int, semitones: int) -> bool:
        """
        Transpose all notes in a track by a specified number of semitones.
        
        Args:
            track_index (int): Index of the track to transpose
            semitones (int): Number of semitones to transpose (positive = up, negative = down)
            
        Returns:
            bool: True if successful, False otherwise
        """
        if self.current_song is None:
            print("No song loaded")
            return False
            
        if track_index < 0 or track_index >= len(self.current_song.tracks):
            print(f"Invalid track index: {track_index}")
            return False
            
        track = self.current_song.tracks[track_index]
        strings = {s.number for s in track.strings}
        notes = [note for measure in track.measures for voice in measure.voices
                 for beat in voice.beats for note in beat.notes
                 if note.type != NoteType.dead and note.string in strings]
        # All-or-nothing: refuse if any note would leave the fretboard.
        bad = [n for n in notes if not 0 <= n.value + semitones <= track.fretCount]
        if bad:
            raise ValueError(f"{len(bad)} notes would fall outside frets 0-{track.fretCount}; "
                             f"re-finger them first (e.g. with edit_notes)")
        for note in notes:
            note.value += semitones
        return True
            
    def get_track_tab(self, track_index: int) -> str:
        """
        Generate an ASCII tab representation of a track.
        
        Args:
            track_index (int): Index of the track
            
        Returns:
            str: ASCII tab representation
        """
        if self.current_song is None:
            return "No song loaded"
            
        if track_index < 0 or track_index >= len(self.current_song.tracks):
            return f"Invalid track index: {track_index}"
            
        track = self.current_song.tracks[track_index]
        tab_lines = []
        
        # Get number of strings
        strings = track.strings
        if not strings:
            return "No strings in track"
            
        # Add track name
        tab_lines.append(f"Track: {track.name}")
        tab_lines.append("")
        
        # Add tuning information
        tab_lines.append("Tuning: " + " ".join([self._note_from_value(s.value) for s in sorted(strings, key=lambda s: s.number)]))
        tab_lines.append("")
        
        # Initialize tab strings
        string_lines = ["" for _ in range(len(strings))]
        
        # Process each measure
        for measure_index, measure in enumerate(track.measures):
            # Add measure number
            measure_header = f"|-{measure_index + 1}-"
            for string_index in range(len(strings)):
                string_lines[string_index] += measure_header
            
            max_measure_width = len(measure_header)
            
            # Process each voice, beat, note
            for voice in measure.voices:
                voice_width = 0
                temp_string_lines = ["" for _ in range(len(strings))]
                
                for beat in voice.beats:
                    # Initialize beat representation for each string
                    for i in range(len(strings)):
                        temp_string_lines[i] += "-"
                    
                    # Add notes
                    for note in beat.notes:
                        string_index = len(strings) - note.string
                        
                        if 0 <= string_index < len(strings):
                            # Replace the last character with note value
                            fret_str = str(note.value)
                            temp_string_lines[string_index] = temp_string_lines[string_index][:-1] + fret_str
                            
                            # Add dashes after the note to align
                            fret_len = len(fret_str)
                            if fret_len > 1:
                                for i in range(len(strings)):
                                    if i != string_index:
                                        temp_string_lines[i] += "-" * (fret_len - 1)
                    
                    # Add duration spacing
                    duration_space = 1
                    for i in range(len(strings)):
                        temp_string_lines[i] += "-" * duration_space
                    
                    voice_width += 1 + duration_space
                
                # Update max width
                max_measure_width = max(max_measure_width, voice_width)
                
                # Add voice lines to main string lines
                for i in range(len(strings)):
                    string_lines[i] += temp_string_lines[i]
            
            # Add measure ending
            for i in range(len(strings)):
                string_lines[i] += "|"
            
            # Add newline after every 4 measures
            if (measure_index + 1) % 4 == 0:
                tab_lines.extend(string_lines)
                tab_lines.append("")
                string_lines = ["" for _ in range(len(strings))]
        
        # Add any remaining measures
        if any(line for line in string_lines):
            tab_lines.extend(string_lines)
        
        return "\n".join(tab_lines)
        
    def _note_from_value(self, value: int) -> str:
        """Convert a MIDI note value to note name."""
        notes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        octave = value // 12 - 1
        note = value % 12
        return f"{notes[note]}{octave}"

    def get_track_info(self, track_number: int) -> dict:
        """Get information about a specific track."""
        self._ensure_song_loaded()
        if not 0 <= track_number < len(self.current_song.tracks):
            raise ValueError(f"Invalid track number: {track_number}")
            
        track = self.current_song.tracks[track_number]
        return {
            'name': track.name,
            'instrument': track.instrument.name,
            'is_solo': track.isSolo,
            'is_mute': track.isMute,
            'measure_count': len(track.measures)
        } 