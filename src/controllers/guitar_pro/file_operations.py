import os
import tempfile

from guitarpro import parse, write
from .base_controller import GuitarProMixin
import logging

logger = logging.getLogger(__name__)

class FileOperationsController(GuitarProMixin):
    """Controller for Guitar Pro file operations."""
    
    def load_file(self, file_path: str) -> None:
        """Load a Guitar Pro file."""
        try:
            logger.info(f"Loading Guitar Pro file: {file_path}")
            self.current_song = parse(file_path)
            
            # Log song details
            logger.info(f"File loaded successfully. Song details:")
            logger.info(f"- Title: {self.current_song.title}")
            logger.info(f"- Artist: {self.current_song.artist}")
            logger.info(f"- Tracks: {len(self.current_song.tracks)}")
            
            # Log track details
            for i, track in enumerate(self.current_song.tracks):
                logger.info(f"Track {i}:")
                logger.info(f"- Name: {track.name}")
                logger.info(f"- Strings: {len(track.strings)}")
                logger.info(f"- Measures: {len(track.measures)}")

        except Exception as e:
            logger.error(f"Error loading file: {e}")
            raise
        
    def save_file(self, file_path: str) -> None:
        """Save the current song to a Guitar Pro file."""
        self._ensure_song_loaded()
        self._check_writable()
        # Write to a temp file first so a failed write can't clobber the target.
        directory, name = os.path.split(os.path.abspath(file_path))
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=f".{name}.", suffix=os.path.splitext(name)[1])
        os.close(fd)
        try:
            write(self.current_song, tmp_path)
            # mkstemp creates owner-only files; keep the target's mode, or the umask default.
            if os.path.exists(file_path):
                mode = os.stat(file_path).st_mode & 0o777
            else:
                umask = os.umask(0)
                os.umask(umask)
                mode = 0o666 & ~umask
            os.chmod(tmp_path, mode)
            os.replace(tmp_path, file_path)
        except BaseException:
            os.remove(tmp_path)
            raise
            
    def export_to_midi(self, file_path: str) -> bool:
        """
        Export the current song to MIDI format.
        
        Args:
            file_path (str): Path to save the MIDI file
            
        Returns:
            bool: True if successful, False otherwise
        """
        if self.current_song is None:
            print("No song loaded")
            return False
        
        try:
            # First try to use our custom MIDI exporter
            try:
                from utils.midi_export import convert_to_midi
                return convert_to_midi(self.current_song, file_path)
            except ImportError:
                # Fall back to using the guitarpro.models module if it provides write_midi
                try:
                    from guitarpro.models import write_midi
                    write_midi(self.current_song, file_path)
                    return True
                except (ImportError, AttributeError):
                    # If neither method works, raise an exception
                    raise ImportError("No MIDI export method available")
        except Exception as e:
            print(f"Error exporting to MIDI: {e}")
            return False
            
    def export_to_json(self, file_path: str) -> bool:
        """
        Export the current song to a JSON file.
        
        Args:
            file_path (str): Path to save the JSON file
            
        Returns:
            bool: True if successful, False otherwise
        """
        if self.current_song is None:
            print("No song loaded")
            return False
            
        try:
            from utils.json_export import export_to_json
            return export_to_json(self.current_song, file_path)
        except Exception as e:
            print(f"Error exporting to JSON: {e}")
            return False
    
    def import_from_json(self, file_path: str) -> bool:
        """
        Import a song from a JSON file.
        
        Args:
            file_path (str): Path to the JSON file
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            from utils.json_export import import_from_json
            import guitarpro as gp
            
            song = import_from_json(file_path, gp)
            if song:
                self.current_song = song
                return True
            return False
        except Exception as e:
            print(f"Error importing from JSON: {e}")
            return False

    def _check_writable(self) -> None:
        """Refuse songs the GP5 writer would silently corrupt.

        Beats store their notes as a bitmask of strings, so two notes on one
        string, or a note on a string the track doesn't have, shift every
        byte after it and the file can't be read back.
        """
        problems = []
        for t, track in enumerate(self.current_song.tracks):
            count = len(track.strings)
            for m, measure in enumerate(track.measures):
                for v, voice in enumerate(measure.voices):
                    for b, beat in enumerate(voice.beats):
                        strings = [n.string for n in beat.notes]
                        if len(strings) != len(set(strings)) or any(not 1 <= s <= count for s in strings):
                            problems.append(f"track {t} measure {m} voice {v} beat {b} strings {sorted(strings)}")
        if problems:
            raise ValueError(f"{len(problems)} beats have two notes on one string or an invalid "
                             f"string, which Guitar Pro files can't store (fix with suggest_fingering "
                             f"or delete_notes): " + "; ".join(problems[:5]))

