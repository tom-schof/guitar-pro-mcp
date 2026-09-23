import copy
from typing import Any, Dict, List, Optional

from .base_controller import GuitarProMixin
from guitarpro.models import Beat, BeatStatus, Note, NoteType, Track

class EditOperationsController(GuitarProMixin):
    """Controller for reading and restructuring existing song content.

    Notes are addressed as {"measure", "voice", "beat", "string"} (all 0-based
    except string, which is 1-based as in Guitar Pro). Batch operations
    validate every address before mutating anything.
    """

    # ----- helpers -------------------------------------------------------

    def _track(self, track_index: int) -> Track:
        self._ensure_song_loaded()
        tracks = self.current_song.tracks
        if not 0 <= track_index < len(tracks):
            raise ValueError(f"Invalid track index {track_index} (song has {len(tracks)} tracks)")
        return tracks[track_index]

    def _beat(self, track: Track, measure: int, voice: int, beat: int) -> Beat:
        if not 0 <= measure < len(track.measures):
            raise ValueError(f"Invalid measure {measure}")
        voices = track.measures[measure].voices
        if not 0 <= voice < len(voices):
            raise ValueError(f"Invalid voice {voice} in measure {measure}")
        beats = voices[voice].beats
        if not 0 <= beat < len(beats):
            raise ValueError(f"Invalid beat {beat} in measure {measure} voice {voice}")
        return beats[beat]

    def _note(self, track: Track, addr: Dict[str, int]) -> Note:
        beat = self._beat(track, addr["measure"], addr.get("voice", 0), addr["beat"])
        # An optional "fret" picks between two notes clashing on one string.
        for note in beat.notes:
            if note.string == addr["string"] and addr.get("fret", note.value) == note.value:
                return note
        raise ValueError(f"No note on string {addr['string']} at {addr}")

    @staticmethod
    def _beat_ticks(voice) -> List[int]:
        """Start tick of each beat relative to the start of its measure."""
        ticks, pos = [], 0
        for beat in voice.beats:
            ticks.append(pos)
            pos += beat.duration.time
        return ticks

    @staticmethod
    def _all_beats(track: Track):
        for measure in track.measures:
            for voice in measure.voices:
                yield from voice.beats

    @staticmethod
    def _repair_ties(track: Track) -> None:
        """Turn tie notes whose preceding beat has no note on that string into normal notes.

        Deleting or moving a note can strand the notes tied to it; Guitar Pro
        would otherwise draw a tie from nothing.
        """
        for v in range(max((len(m.voices) for m in track.measures), default=0)):
            prev = None
            for measure in track.measures:
                if v >= len(measure.voices):
                    prev = None
                    continue
                for beat in measure.voices[v].beats:
                    held = {n.string for n in prev.notes} if prev else set()
                    for note in beat.notes:
                        if note.type == NoteType.tie and note.string not in held:
                            note.type = NoteType.normal
                    prev = beat

    @staticmethod
    def _refresh_status(beat: Beat) -> None:
        beat.status = BeatStatus.normal if beat.notes else BeatStatus.rest

    def _check_fret(self, track: Track, string: int, fret: int) -> None:
        if not 1 <= string <= len(track.strings):
            raise ValueError(f"String {string} out of range 1-{len(track.strings)}")
        if not 0 <= fret <= track.fretCount:
            raise ValueError(f"Fret {fret} out of range 0-{track.fretCount}")

    def _tuning(self, track: Track, string: int) -> int:
        return next(s.value for s in track.strings if s.number == string)

    # ----- reading -------------------------------------------------------

    def get_measures(self, track_index: int, start_measure: int = 0,
                     end_measure: Optional[int] = None) -> List[Dict[str, Any]]:
        """Beat-level view of a measure range (end inclusive), with pitches and tick offsets."""
        track = self._track(track_index)
        end = len(track.measures) - 1 if end_measure is None else min(end_measure, len(track.measures) - 1)
        result = []
        for m in range(start_measure, end + 1):
            measure = track.measures[m]
            voices = []
            for v, voice in enumerate(measure.voices):
                ticks = self._beat_ticks(voice)
                beats = []
                for b, beat in enumerate(voice.beats):
                    if beat.status == BeatStatus.empty and not beat.notes:
                        continue
                    beats.append({
                        "beat": b,
                        "tick": ticks[b],
                        "duration": beat.duration.value,
                        "dotted": beat.duration.isDotted,
                        "tuplet": f"{beat.duration.tuplet.enters}:{beat.duration.tuplet.times}",
                        "ticks": beat.duration.time,
                        "rest": not beat.notes,
                        "notes": [{
                            "string": n.string,
                            "fret": n.value,
                            "pitch": n.realValue,
                            "type": n.type.name,
                        } for n in sorted(beat.notes, key=lambda n: n.string)],
                    })
                if beats:
                    voices.append({"voice": v, "beats": beats})
            result.append({
                "measure": m,
                "time_signature": f"{measure.header.timeSignature.numerator}/"
                                  f"{measure.header.timeSignature.denominator.value}",
                "voices": voices,
            })
        return result

    def find_playability_issues(self, track_index: int, max_fret_span: int = 5,
                                max_position_jump: int = 7) -> List[Dict[str, Any]]:
        """Flag beats a single guitarist likely can't play.

        Checks: two notes on one string, more notes than strings, fretted
        stretch wider than max_fret_span, and position jumps between
        consecutive beats larger than max_position_jump frets.
        """
        track = self._track(track_index)
        issues = []
        for m, measure in enumerate(track.measures):
            for v, voice in enumerate(measure.voices):
                prev_pos = None
                for b, beat in enumerate(voice.beats):
                    if not beat.notes:
                        continue
                    where = {"measure": m, "voice": v, "beat": b}
                    strings = [n.string for n in beat.notes]
                    if len(strings) != len(set(strings)):
                        issues.append({**where, "issue": "string_clash", "strings": strings})
                    if len(beat.notes) > len(track.strings):
                        issues.append({**where, "issue": "too_many_notes", "count": len(beat.notes)})
                    fretted = [n.value for n in beat.notes if n.value > 0 and n.type != NoteType.dead]
                    if fretted:
                        span = max(fretted) - min(fretted)
                        if span > max_fret_span:
                            issues.append({**where, "issue": "wide_stretch", "span": span,
                                           "frets": sorted(fretted)})
                        pos = min(fretted)
                        if prev_pos is not None and abs(pos - prev_pos) > max_position_jump:
                            issues.append({**where, "issue": "position_jump",
                                           "from_fret": prev_pos, "to_fret": pos})
                        prev_pos = pos
        # Notes sounding in both voices at once are also a single-player red flag.
        for m, measure in enumerate(track.measures):
            active = [v for v in measure.voices if any(b.notes for b in v.beats)]
            if len(active) > 1:
                issues.append({"measure": m, "issue": "multiple_voices", "voices": len(active)})
        return issues

    # ----- track structure -----------------------------------------------

    def duplicate_track(self, track_index: int, name: Optional[str] = None,
                        clear_notes: bool = False) -> int:
        """Deep-copy a track (measures, notes, tuning, settings) and append it.

        With clear_notes, the copy keeps the rhythm (every beat, now a rest)
        but no notes, ready to receive notes from move_notes.
        """
        source = self._track(track_index)
        song = self.current_song
        # Share the song and measure headers instead of copying them.
        memo = {id(song): song}
        for header in song.measureHeaders:
            memo[id(header)] = header
        new_track = copy.deepcopy(source, memo)
        new_track.name = name or f"{source.name} (copy)"
        new_track.channel.channel, new_track.channel.effectChannel = self._free_channels(source)
        if clear_notes:
            for beat in self._all_beats(new_track):
                if beat.notes:
                    beat.notes = []
                    beat.status = BeatStatus.rest
        song.tracks.append(new_track)
        self._renumber_tracks()
        return len(song.tracks) - 1

    def delete_track(self, track_index: int) -> None:
        self._track(track_index)
        if len(self.current_song.tracks) == 1:
            raise ValueError("Cannot delete the only track")
        del self.current_song.tracks[track_index]
        self._renumber_tracks()

    def _renumber_tracks(self) -> None:
        for i, track in enumerate(self.current_song.tracks):
            track.number = i + 1

    # ----- note edits ----------------------------------------------------

    def delete_notes(self, track_index: int, notes: List[Dict[str, int]]) -> int:
        """Delete notes; beats left empty become rests (timing is preserved)."""
        track = self._track(track_index)
        targets = [(self._beat(track, a["measure"], a.get("voice", 0), a["beat"]), self._note(track, a))
                   for a in notes]
        for beat, note in targets:
            if note in beat.notes:
                beat.notes.remove(note)
            self._refresh_status(beat)
        self._repair_ties(track)
        return len(targets)

    def edit_notes(self, track_index: int, edits: List[Dict[str, Any]]) -> int:
        """Change string/fret of notes.

        Each edit is a note address plus any of: new_string, new_fret,
        keep_pitch (default true: new_fret is derived so the pitch is unchanged
        when only new_string is given).
        """
        track = self._track(track_index)
        plan = []
        for e in edits:
            beat = self._beat(track, e["measure"], e.get("voice", 0), e["beat"])
            note = self._note(track, e)
            new_string = e.get("new_string", note.string)
            if "new_fret" in e:
                new_fret = e["new_fret"]
            elif e.get("keep_pitch", True):
                new_fret = note.realValue - self._tuning(track, new_string)
            else:
                new_fret = note.value
            self._check_fret(track, new_string, new_fret)
            plan.append((beat, note, new_string, new_fret))
        # Validate string clashes against the post-edit state of each beat.
        final: Dict[int, Dict[int, int]] = {}
        for beat, note, new_string, _ in plan:
            final.setdefault(id(beat), {id(n): n.string for n in beat.notes})[id(note)] = new_string
        for beat, _, _, _ in plan:
            strings = list(final[id(beat)].values())
            if len(strings) != len(set(strings)):
                raise ValueError(f"Edits would put two notes on one string: {sorted(strings)}")
        for _, note, new_string, new_fret in plan:
            note.string, note.value = new_string, new_fret
        return len(plan)

    def add_notes(self, track_index: int, notes: List[Dict[str, Any]]) -> int:
        """Add notes to existing beats: address plus "fret" (and optional "velocity", "type")."""
        track = self._track(track_index)
        plan = []
        for a in notes:
            beat = self._beat(track, a["measure"], a.get("voice", 0), a["beat"])
            self._check_fret(track, a["string"], a["fret"])
            if any(n.string == a["string"] for n in beat.notes):
                raise ValueError(f"String {a['string']} already has a note at {a}")
            plan.append((beat, a))
        for beat, a in plan:
            note = Note(beat, value=a["fret"], string=a["string"],
                        velocity=a.get("velocity", 95),
                        type=NoteType[a.get("type", "normal")])
            beat.notes.append(note)
            self._refresh_status(beat)
        return len(plan)

    def move_notes(self, source_track: int, dest_track: int, notes: List[Dict[str, int]]) -> int:
        """Move notes to another track at the same measure, voice and tick.

        The destination needs a beat starting at the same tick (true for
        tracks made with duplicate_track). The note keeps its string and
        fret, effects and velocity.
        """
        src = self._track(source_track)
        dst = self._track(dest_track)
        if len(dst.strings) != len(src.strings):
            raise ValueError("Source and destination tracks have different string counts")
        plan = []
        for a in notes:
            m, v, b = a["measure"], a.get("voice", 0), a["beat"]
            src_beat = self._beat(src, m, v, b)
            note = self._note(src, a)
            tick = self._beat_ticks(src.measures[m].voices[v])[b]
            if m >= len(dst.measures) or v >= len(dst.measures[m].voices):
                raise ValueError(f"Destination has no measure {m} voice {v}")
            dst_voice = dst.measures[m].voices[v]
            dst_ticks = self._beat_ticks(dst_voice)
            if tick not in dst_ticks:
                raise ValueError(f"Destination has no beat at tick {tick} in measure {m} voice {v}")
            dst_beat = dst_voice.beats[dst_ticks.index(tick)]
            if dst_beat.duration.time != src_beat.duration.time:
                raise ValueError(f"Destination beat at tick {tick} in measure {m} has a different duration")
            if any(n.string == note.string for n in dst_beat.notes):
                raise ValueError(f"Destination already has a note on string {note.string} at {a}")
            plan.append((src_beat, note, dst_beat))
        for src_beat, note, dst_beat in plan:
            src_beat.notes.remove(note)
            note.beat = dst_beat
            dst_beat.notes.append(note)
            self._refresh_status(src_beat)
            self._refresh_status(dst_beat)
        self._repair_ties(src)
        self._repair_ties(dst)
        return len(plan)

    # ----- splitting -----------------------------------------------------

    def split_track(self, track_index: int, mode: str, name: Optional[str] = None,
                    dest_track: Optional[int] = None, start_measure: int = 0,
                    end_measure: Optional[int] = None, split_pitch: Optional[int] = None,
                    strings: Optional[List[int]] = None, min_gap: int = 0,
                    notes: Optional[List[Dict[str, int]]] = None) -> Dict[str, Any]:
        """Move a selection of notes out of a track into another track.

        Modes (applied within the measure range):
        - "pitch": notes with MIDI pitch >= split_pitch.
        - "top_note": the highest note of each beat with 2+ notes, if it is at
          least min_gap semitones above the next note; a lone note moves only
          if split_pitch is given and it is >= split_pitch.
        - "strings": notes on the given strings.
        - "notes": the given note addresses.

        Without dest_track, a new track with the source's rhythm is created.
        Nothing changes if no note matches or any move is invalid.
        """
        src = self._track(track_index)
        if mode == "notes":
            if not notes:
                raise ValueError("mode 'notes' needs notes")
            selected = list(notes)
        else:
            if mode == "pitch" and split_pitch is None:
                raise ValueError("mode 'pitch' needs split_pitch")
            if mode == "strings" and not strings:
                raise ValueError("mode 'strings' needs strings")
            if mode not in ("pitch", "top_note", "strings"):
                raise ValueError(f"Unknown mode {mode!r}; use pitch, top_note, strings or notes")
            last = len(src.measures) - 1 if end_measure is None else min(end_measure, len(src.measures) - 1)
            selected = []
            for m in range(start_measure, last + 1):
                for v, voice in enumerate(src.measures[m].voices):
                    for b, beat in enumerate(voice.beats):
                        picked = self._split_pick(beat, mode, split_pitch, strings, min_gap)
                        selected += [{"measure": m, "voice": v, "beat": b, "string": n.string}
                                     for n in picked]
            if not selected:
                raise ValueError("No notes matched; nothing changed")
        if dest_track is None:
            dest = self.duplicate_track(track_index, name or f"{src.name} (split)", clear_notes=True)
            try:
                moved = self.move_notes(track_index, dest, selected)
            except Exception:
                self.delete_track(dest)
                raise
        else:
            if dest_track == track_index:
                raise ValueError("dest_track must differ from track_index")
            dest = dest_track
            moved = self.move_notes(track_index, dest, selected)
        return {"track_index": dest, "moved": moved}

    @staticmethod
    def _split_pick(beat: Beat, mode: str, split_pitch: Optional[int],
                    strings: Optional[List[int]], min_gap: int) -> List[Note]:
        if not beat.notes:
            return []
        if mode == "pitch":
            return [n for n in beat.notes if n.realValue >= split_pitch]
        if mode == "strings":
            return [n for n in beat.notes if n.string in strings]
        ordered = sorted(beat.notes, key=lambda n: n.realValue, reverse=True)
        if len(ordered) >= 2:
            return [ordered[0]] if ordered[0].realValue - ordered[1].realValue >= min_gap else []
        return [ordered[0]] if split_pitch is not None and ordered[0].realValue >= split_pitch else []
