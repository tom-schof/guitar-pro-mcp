"""
JSON export/import utility for Guitar Pro files.

This module provides functions to convert Guitar Pro song objects to and from JSON format,
allowing for easier interchange with other systems.
"""

import os
import json
from typing import Dict, List, Any, Optional

METADATA_FIELDS = ["title", "subtitle", "artist", "album", "words", "music",
                   "copyright", "tab", "instructions", "tempo"]
CHANNEL_FIELDS = ["instrument", "volume", "balance", "chorus", "reverb", "phaser", "tremolo"]

def song_to_json(song) -> Dict[str, Any]:
    """
    Convert a Guitar Pro song to a JSON-serializable dictionary.
    
    Args:
        song: Guitar Pro song object
        
    Returns:
        dict: JSON-serializable dictionary representing the song
    """
    if song is None:
        return {}
    
    from guitarpro.models import NoteType

    # Basic song metadata
    song_data = {
        "metadata": {field: getattr(song, field) for field in METADATA_FIELDS},
        "tracks": []
    }
    
    # Process tracks
    for track_index, track in enumerate(song.tracks):
        track_data = {
            "name": track.name,
            "index": track_index,
            "is_percussion": track.isPercussionTrack,
            "channel": {field: getattr(track.channel, field) for field in CHANNEL_FIELDS},
            "strings": [{"number": s.number, "value": s.value} for s in track.strings],
            "measures": []
        }
        
        # Process measures, voices, beats, and notes
        for measure_index, measure in enumerate(track.measures):
            ts = measure.header.timeSignature
            measure_data = {
                "index": measure_index,
                "time_signature": [ts.numerator, ts.denominator.value],
                "voices": []
            }
            
            for voice_index, voice in enumerate(measure.voices):
                voice_data = {
                    "index": voice_index,
                    "beats": []
                }
                
                for beat_index, beat in enumerate(voice.beats):
                    tuplet = beat.duration.tuplet
                    beat_data = {
                        "index": beat_index,
                        "duration": {
                            "value": beat.duration.value,
                            "is_dotted": beat.duration.isDotted,
                            "tuplet": [tuplet.enters, tuplet.times],
                            "is_rest": not beat.notes
                        },
                        "notes": []
                    }
                    
                    for note in beat.notes:
                        effect = note.effect
                        note_data = {
                            "string": note.string,
                            "value": note.value,
                            "velocity": note.velocity,
                            "type": note.type.name,
                            "is_tied": note.type == NoteType.tie,
                            "effect": {name: True for name, on in [
                                ("bend", effect.isBend),
                                ("harmonic", effect.isHarmonic),
                                ("ghost", effect.ghostNote),
                                ("slide", bool(effect.slides)),
                                ("vibrato", effect.vibrato),
                                ("hammer", effect.hammer),
                                ("palm_mute", effect.palmMute),
                                ("let_ring", effect.letRing),
                            ] if on}
                        }
                        beat_data["notes"].append(note_data)
                    
                    voice_data["beats"].append(beat_data)
                
                measure_data["voices"].append(voice_data)
            
            track_data["measures"].append(measure_data)
        
        song_data["tracks"].append(track_data)
    
    return song_data

def export_to_json(song, file_path: str) -> bool:
    """
    Export a Guitar Pro song to a JSON file.
    
    Args:
        song: Guitar Pro song object
        file_path (str): Path to save the JSON file
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        song_data = song_to_json(song)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(song_data, f, indent=2, ensure_ascii=False)
        
        return True
    except Exception as e:
        print(f"Error exporting to JSON: {e}")
        return False

def json_to_song(json_data: Dict[str, Any], gp) -> Any:
    """
    Convert JSON data to a Guitar Pro song object.
    
    Args:
        json_data (dict): JSON data representing a Guitar Pro song
        gp: Guitar Pro module (guitarpro)
        
    Returns:
        Any: Guitar Pro song object
    """
    try:
        m = gp.models
        song = m.Song()
        song.tracks = []
        song.measureHeaders = []
        
        # Set metadata
        metadata = json_data.get("metadata", {})
        for field in METADATA_FIELDS:
            if field in metadata:
                setattr(song, field, metadata[field])
        
        tracks_data = json_data.get("tracks", [])
        
        # Measure headers are shared by all tracks; take time signatures from the first.
        first = tracks_data[0].get("measures", []) if tracks_data else []
        measure_count = max((len(t.get("measures", [])) for t in tracks_data), default=0)
        start = m.Duration.quarterTime
        for i in range(max(measure_count, 1)):
            header = m.MeasureHeader(number=i + 1, start=start)
            if i < len(first) and "time_signature" in first[i]:
                numerator, denominator = first[i]["time_signature"]
                header.timeSignature = m.TimeSignature(numerator, m.Duration(value=denominator))
            song.measureHeaders.append(header)
            start += header.length
        
        # Process tracks
        for track_index, track_data in enumerate(tracks_data):
            track = m.Track(song, number=track_index + 1)
            track.name = track_data.get("name", "Track")
            track.isPercussionTrack = track_data.get("is_percussion", False)
            for field, value in track_data.get("channel", {}).items():
                if field in CHANNEL_FIELDS:
                    setattr(track.channel, field, value)
            if track_data.get("strings"):
                track.strings = [m.GuitarString(s["number"], s["value"]) for s in track_data["strings"]]
            
            for measure_index, measure_data in enumerate(track_data.get("measures", [])):
                measure = track.measures[measure_index]
                for voice_index, voice_data in enumerate(measure_data.get("voices", [])[:len(measure.voices)]):
                    voice = measure.voices[voice_index]
                    for beat_data in voice_data.get("beats", []):
                        duration_data = beat_data.get("duration", {})
                        enters, times = duration_data.get("tuplet", [1, 1])
                        beat = m.Beat(voice, duration=m.Duration(
                            value=duration_data.get("value", 4),
                            isDotted=duration_data.get("is_dotted", False),
                            tuplet=m.Tuplet(enters, times)))
                        
                        for note_data in beat_data.get("notes", []):
                            note_type = note_data.get("type") or ("tie" if note_data.get("is_tied") else "normal")
                            note = m.Note(beat, value=note_data.get("value", 0),
                                          velocity=note_data.get("velocity", m.Velocities.default),
                                          string=note_data.get("string", 1),
                                          type=m.NoteType[note_type])
                            effect_data = note_data.get("effect", {})
                            if effect_data.get("bend"):
                                note.effect.bend = m.BendEffect(type=m.BendType.bend, value=100, points=[
                                    m.BendPoint(0, 0), m.BendPoint(6, 4), m.BendPoint(12, 4)])
                            if effect_data.get("harmonic"):
                                note.effect.harmonic = m.NaturalHarmonic()
                            if effect_data.get("slide"):
                                note.effect.slides = [m.SlideType.shiftSlideTo]
                            note.effect.ghostNote = bool(effect_data.get("ghost"))
                            note.effect.vibrato = bool(effect_data.get("vibrato"))
                            note.effect.hammer = bool(effect_data.get("hammer"))
                            note.effect.palmMute = bool(effect_data.get("palm_mute"))
                            note.effect.letRing = bool(effect_data.get("let_ring"))
                            beat.notes.append(note)
                        
                        beat.status = m.BeatStatus.normal if beat.notes else m.BeatStatus.rest
                        voice.beats.append(beat)
            
            song.tracks.append(track)
        
        return song
    except Exception as e:
        print(f"Error converting JSON to song: {e}")
        return None

def import_from_json(file_path: str, gp) -> Optional[Any]:
    """
    Import a song from a JSON file.
    
    Args:
        file_path (str): Path to the JSON file
        gp: Guitar Pro module (guitarpro)
        
    Returns:
        Optional[Any]: Guitar Pro song object, or None if import fails
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        
        return json_to_song(json_data, gp)
    except Exception as e:
        print(f"Error importing from JSON: {e}")
        return None
