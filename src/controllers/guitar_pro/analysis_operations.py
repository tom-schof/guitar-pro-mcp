import itertools
import statistics
from typing import Any, Dict, List, Optional, Tuple

from guitarpro.models import Beat, GuitarString, NoteType, Track

from .edit_operations import EditOperationsController

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# suggest_fingering keeps this many cheapest fingerings per beat before the
# cross-beat search, so long passages stay fast.
MAX_CANDIDATES = 24


def pitch_name(pitch: int) -> str:
    return f"{NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"


class AnalysisOperationsController(EditOperationsController):
    """Track analysis for splitting merged parts and fixing unplayable fingerings."""

    def _range(self, track: Track, start_measure: int, end_measure: Optional[int]) -> range:
        last = len(track.measures) - 1 if end_measure is None else min(end_measure, len(track.measures) - 1)
        return range(max(start_measure, 0), last + 1)

    # ----- part analysis -------------------------------------------------

    def analyze_track(self, track_index: int, start_measure: int = 0,
                      end_measure: Optional[int] = None, melody_gap: int = 7,
                      max_fret_span: int = 5) -> Dict[str, Any]:
        """Describe the texture of a track and whether it mixes parts.

        Each beat is a rest, single note, dyad, chord (3+ notes) or layered
        chord (3+ notes whose top note sits melody_gap+ semitones above the
        rest, i.e. melody over accompaniment; plain chords rarely have more
        than a fourth at the top). Single notes above the typical chord top
        are counted as melody interleaved with chords.
        """
        track = self._track(track_index)
        measures = self._range(track, start_measure, end_measure)
        beats: List[Tuple[int, int, Beat]] = [
            (m, v, beat) for m in measures for v, voice in enumerate(track.measures[m].voices)
            for beat in voice.beats if beat.notes]

        def pitches(beat: Beat) -> List[int]:
            return sorted((n.realValue for n in beat.notes), reverse=True)

        def kind(beat: Beat) -> str:
            p = pitches(beat)
            if len(p) == 1:
                return "single"
            if len(p) == 2:
                return "dyad"
            return "layered" if p[0] - p[1] >= melody_gap else "chord"

        kinds = [kind(b) for _, _, b in beats]
        # Top of the accompaniment: a chord's top note, or a layered chord's second note.
        chord_tops = [pitches(b)[0 if k == "chord" else 1]
                      for (_, _, b), k in zip(beats, kinds) if k in ("chord", "layered")]
        chord_top = statistics.median(chord_tops) if chord_tops else None
        high_single = [k == "single" and chord_top is not None and pitches(b)[0] > chord_top
                       for (_, _, b), k in zip(beats, kinds)]

        # Per-measure texture, grouped into runs of equal texture.
        per_measure: Dict[int, Dict[str, int]] = {m: {} for m in measures}
        voices_used: Dict[int, set] = {m: set() for m in measures}
        for (m, v, _), k, hi in zip(beats, kinds, high_single):
            key = "high_single" if hi else k
            per_measure[m][key] = per_measure[m].get(key, 0) + 1
            voices_used[m].add(v)
        sections: List[Dict[str, Any]] = []
        for m in measures:
            texture = self._texture(per_measure[m], len(voices_used[m]))
            if sections and sections[-1]["texture"] == texture and sections[-1]["end_measure"] == m - 1:
                sections[-1]["end_measure"] = m
            else:
                sections.append({"start_measure": m, "end_measure": m, "texture": texture})

        # Split pitch that best separates melody samples from accompaniment samples.
        melody, accomp = [], []
        for (_, _, b), k, hi in zip(beats, kinds, high_single):
            p = pitches(b)
            if k == "layered":
                melody.append(p[0])
                accomp.extend(p[1:])
            elif hi:
                melody.append(p[0])
            elif k == "chord":
                accomp.extend(p)
        split_pitch, split_errors = self._best_split(melody, accomp)

        issues = [i for i in self.find_playability_issues(track_index, max_fret_span)
                  if i["measure"] in measures]
        issue_counts: Dict[str, int] = {}
        for i in issues:
            issue_counts[i["issue"]] = issue_counts.get(i["issue"], 0) + 1

        mixed = kinds.count("layered") + sum(high_single)
        reasons = []
        if mixed >= max(2, len(beats) // 20):
            reasons.append(f"{mixed} beats have melody above chords")
        merged = issue_counts.get("too_many_notes", 0) + issue_counts.get("string_clash", 0)
        if merged:
            reasons.append(f"{merged} beats have more notes than one player can fret")
        if issue_counts.get("multiple_voices"):
            reasons.append(f"{issue_counts['multiple_voices']} measures use two voices at once")

        suggestion = None
        if reasons and split_pitch is not None:
            if melody and split_errors <= 0.1 * (len(melody) + len(accomp)):
                suggestion = {"tool": "split_track", "mode": "pitch", "split_pitch": split_pitch}
            else:
                suggestion = {"tool": "split_track", "mode": "top_note", "min_gap": melody_gap,
                              "split_pitch": split_pitch}

        all_pitches = [p for _, _, b in beats for p in pitches(b)]
        return {
            "track": track_index,
            "name": track.name,
            "measures": [measures.start, measures.stop - 1] if measures else [],
            "note_count": len(all_pitches),
            "range": ([pitch_name(min(all_pitches)), pitch_name(max(all_pitches))]
                      if all_pitches else None),
            "max_notes_per_beat": max((len(b.notes) for _, _, b in beats), default=0),
            "beat_kinds": {k: kinds.count(k) for k in ("single", "dyad", "chord", "layered")},
            "melody_notes_between_chords": sum(high_single),
            "typical_chord_top": pitch_name(int(chord_top)) if chord_top is not None else None,
            "suggested_split_pitch": split_pitch,
            "suggested_split_note": pitch_name(split_pitch) if split_pitch is not None else None,
            "playability_issues": issue_counts,
            "should_split": bool(reasons),
            "reasons": reasons,
            "suggestion": suggestion,
            "sections": sections,
        }

    def split_parts(self, track_index: int, start_measure: int = 0,
                    end_measure: Optional[int] = None, rhythm_max_bottom: int = 57,
                    rhythm_max_top: int = 69, bass_max: int = 52, chord_gap: int = 7,
                    melody_name: Optional[str] = None,
                    rhythm_name: Optional[str] = None) -> Dict[str, Any]:
        """Move the rhythm-guitar chords out of a merged part; the lead line stays.

        Splits by role, not by pitch: a chord attacked together and built up
        from the bass is rhythm. It starts at or below rhythm_max_bottom and
        stacks notes within chord_gap semitones (an octave for the bottom
        pair, up to a minor seventh while at or below rhythm_max_top). It
        must have two notes, unless a rhythm chord is still ringing or the
        note is at or below bass_max. Tie notes sustaining rhythm notes are
        rhythm too. Everything else stays, so arpeggios and licks keep their
        continuity. Measures analyze_track calls "chords"
        go entirely to rhythm. Run merge_voices first if both voices carry
        the part, and make_monophonic afterwards to pull double-stops out of
        the lead into a harmony track. All-or-nothing.
        """
        track = self._track(track_index)
        measures = self._range(track, start_measure, end_measure)
        sections = self.analyze_track(track_index, start_measure, end_measure)["sections"]
        chord_only = {m for s in sections if s["texture"].startswith("chords")
                      for m in range(s["start_measure"], s["end_measure"] + 1)}
        moves: List[Dict[str, int]] = []
        for v in range(max((len(m.voices) for m in track.measures), default=0)):
            prev_rhythm: set = set()  # (string, fret) of rhythm notes in the previous beat
            for m, b, beat in self._flat_voice(track, v):
                # Tie notes sustain whatever part their origin went to.
                rhythm = {id(n) for n in beat.notes
                          if n.type == NoteType.tie and (n.string, n.value) in prev_rhythm}
                attacks = sorted((n for n in beat.notes if n.type != NoteType.tie), key=lambda n: n.realValue)
                if m in chord_only:
                    rhythm.update(id(n) for n in attacks)
                else:
                    cluster = attacks[:1]
                    for n in attacks[1:]:
                        gap = n.realValue - cluster[-1].realValue
                        if (gap <= chord_gap or (gap == 12 and len(cluster) == 1)
                                or (gap <= 10 and n.realValue <= rhythm_max_top)):
                            cluster.append(n)
                        else:
                            break
                    ringing = bool(rhythm)  # a rhythm chord is held into this beat
                    if cluster and cluster[0].realValue <= rhythm_max_bottom and (
                            len(cluster) >= 2 or ringing or cluster[0].realValue <= bass_max):
                        rhythm.update(id(n) for n in cluster)
                if m in measures:
                    moves += [{"measure": m, "voice": v, "beat": b, "string": n.string, "fret": n.value}
                              for n in beat.notes if id(n) in rhythm]
                prev_rhythm = {(n.string, n.value) for n in beat.notes if id(n) in rhythm}

        result: Dict[str, Any] = {"melody_track": track_index, "rhythm_track": None}
        if moves:
            split = self.split_track(track_index, "notes", rhythm_name or f"{track.name} (Rhythm)",
                                     notes=moves)
            result["rhythm_track"] = split["track_index"]
        if melody_name:
            track.name = melody_name
        result["moved_to_rhythm"] = len(moves)
        result["chord_only_measures"] = sorted(chord_only)
        return result

    @staticmethod
    def _texture(counts: Dict[str, int], voices: int) -> str:
        if not counts:
            return "rest"
        chordal = counts.get("chord", 0) + counts.get("dyad", 0) + counts.get("layered", 0)
        if counts.get("layered") or (chordal and counts.get("high_single")):
            texture = "melody+chords"
        elif chordal >= counts.get("single", 0) + counts.get("high_single", 0):
            texture = "chords"
        else:
            texture = "single_notes"
        return f"{texture} (2 voices)" if voices > 1 else texture

    @staticmethod
    def _best_split(melody: List[int], accomp: List[int]) -> Tuple[Optional[int], int]:
        """Lowest-error pitch p where melody >= p and accompaniment < p."""
        if not melody or not accomp:
            return None, 0
        best: List[int] = []
        best_err = None
        for p in range(min(melody + accomp), max(melody + accomp) + 2):
            err = sum(x < p for x in melody) + sum(x >= p for x in accomp)
            if best_err is None or err < best_err:
                best, best_err = [p], err
            elif err == best_err:
                best.append(p)
        # Middle of the best run keeps the split away from both parts.
        return best[len(best) // 2], best_err

    # ----- fingering -----------------------------------------------------

    def suggest_fingering(self, track_index: int, start_measure: int = 0,
                          end_measure: Optional[int] = None, max_fret_span: int = 5,
                          max_position_jump: int = 7, apply: bool = False) -> Dict[str, Any]:
        """Re-finger a passage to cut stretches, string clashes and position jumps.

        Keeps every pitch. Searches the string choices for each beat and picks
        the sequence with the lowest total cost across the passage (stretch,
        movement, and a small cost per changed note, so playable fingerings
        stay put). A tie chain moves string as a whole. With apply=True the
        edits are made through edit_notes.
        """
        track = self._track(track_index)
        measures = self._range(track, start_measure, end_measure)
        tuning = {s.number: s.value for s in track.strings}
        edits: List[Dict[str, Any]] = []
        unplayable: List[Dict[str, int]] = []
        before = {"wide_stretch": 0, "string_clash": 0, "position_jump": 0}
        after = dict(before)

        for v, seq in self._voice_sequences(track, measures):
            items = [[(n.realValue, (n.string, n.value)) for n in beat.notes] for _, _, beat, _ in seq]
            path, bad = self._finger(seq, items, tuning, track.fretCount, max_fret_span, max_position_jump)
            unplayable += [{"measure": seq[i][0], "voice": v, "beat": seq[i][1]} for i in bad]
            prev_cur = prev_new = None
            for (m, b, beat, _), chosen in zip(seq, path):
                current = tuple((n.string, n.value) for n in beat.notes)
                self._tally(before, current, prev_cur, max_fret_span, max_position_jump)
                self._tally(after, chosen, prev_new, max_fret_span, max_position_jump)
                prev_cur, prev_new = current, chosen
                for note, (s, f) in zip(beat.notes, chosen):
                    if (s, f) != (note.string, note.value):
                        edits.append({"measure": m, "voice": v, "beat": b, "string": note.string,
                                      "fret": note.value, "new_string": s, "new_fret": f})
        if apply and edits:
            self.edit_notes(track_index, edits)
            self._repair_ties(track)
        return {"edits": edits, "changed_notes": len(edits), "applied": bool(apply and edits),
                "before": before, "after": after, "unplayable_beats": unplayable}

    def retune_track(self, track_index: int, tuning: Optional[List[int]] = None,
                     fret_count: int = 24, octave_shift: bool = True, reduce_chords: bool = False,
                     max_fret_span: int = 5, max_position_jump: int = 7) -> Dict[str, Any]:
        """Change a track's tuning and fret count, keeping every pitch.

        tuning lists the open-string MIDI pitches from string 1 (highest)
        down; the default is standard 6-string guitar. Notes the new
        instrument can't reach are moved by octaves when octave_shift is
        true (otherwise the call fails). With reduce_chords, beats that can't
        be fingered first move a note an octave (reported in
        chord_notes_moved_octave), and only if that fails drop notes (held
        notes first, then doubled and inner notes) until they can. Every note is then re-fingered as in
        suggest_fingering. All-or-nothing.
        """
        track = self._track(track_index)
        tuning = tuning or [64, 59, 55, 50, 45, 40]
        new_tuning = {i + 1: p for i, p in enumerate(tuning)}
        low, high = min(tuning), max(tuning) + fret_count

        def fit(pitch: int) -> int:
            while pitch > high:
                pitch -= 12
            while pitch < low:
                pitch += 12
            return pitch

        def playable(notes, extra: Optional[int] = None) -> bool:
            pitches = [fit(n.realValue) for n in notes] + ([extra] if extra is not None else [])
            return bool(self._candidates([(p, None) for p in pitches], new_tuning, fret_count, {}, set()))

        work = self._copy_track(track)
        dropped, octaved = [], []
        if reduce_chords:
            for v in range(max((len(m.voices) for m in work.measures), default=0)):
                flat = self._flat_voice(work, v)
                for k, (m, b, beat) in enumerate(flat):
                    while beat.notes and not playable(beat.notes):
                        order = self._drop_order(beat)
                        # Prefer moving one note an octave (down, then up) over dropping it.
                        moved = next(((n, d) for n in order for d in (-12, 12)
                                      if n.type != NoteType.tie and low <= n.realValue + d <= high
                                      and playable([x for x in beat.notes if x is not n], extra=n.realValue + d)),
                                     None)
                        if moved:
                            note, d = moved
                            octaved.append({"measure": m, "voice": v, "beat": b,
                                            "from": pitch_name(note.realValue),
                                            "to": pitch_name(note.realValue + d)})
                            for _, n in self._chain(flat, k, note):
                                n.value += d  # string is re-chosen by the fingering pass
                            continue
                        # Otherwise drop the first note (in priority order) that makes it playable.
                        note = next((n for n in order if playable([x for x in beat.notes if x is not n])),
                                    order[0])
                        dropped.append({"measure": m, "voice": v, "beat": b, "pitch": pitch_name(note.realValue)})
                        self._take_chain(flat, k, note)

        measures = range(len(work.measures))
        shifted, plan, bad_beats = 0, [], []
        for v, seq in self._voice_sequences(work, measures):
            items = []
            for m, b, beat, _ in seq:
                item = []
                for n in beat.notes:
                    pitch = fit(n.realValue)
                    if pitch != n.realValue:
                        if not octave_shift:
                            raise ValueError(f"Pitch {pitch_name(n.realValue)} at measure {m} beat {b} "
                                             f"is out of range")
                        shifted += 1
                    current = ((n.string, n.value)
                               if new_tuning.get(n.string, -99) + n.value == pitch and 0 <= n.value <= fret_count
                               else None)
                    item.append((pitch, current))
                items.append(item)
            path, bad = self._finger(seq, items, new_tuning, fret_count, max_fret_span, max_position_jump)
            bad_beats += [{"measure": seq[i][0], "voice": v, "beat": seq[i][1],
                           "notes": len(seq[i][2].notes)} for i in bad]
            plan += [(beat, path[i]) for i, (_, _, beat, _) in enumerate(seq)]
        if bad_beats:
            raise ValueError(f"{len(bad_beats)} beats can't be fingered on the new tuning "
                             f"(too many notes for the strings), e.g. {bad_beats[:5]}; "
                             f"use reduce_chords, or split or delete notes first")
        changed = 0
        work.strings = [GuitarString(i + 1, p) for i, p in enumerate(tuning)]
        work.fretCount = fret_count
        for beat, chosen in plan:
            for note, (s, f) in zip(beat.notes, chosen):
                changed += (s, f) != (note.string, note.value)
                note.string, note.value = s, f
        self._repair_ties(work)
        self.current_song.tracks[track_index] = work
        return {"strings": len(tuning), "fret_count": fret_count, "octave_shifted_notes": shifted,
                "refingered_notes": changed, "chord_notes_moved_octave": octaved,
                "dropped_notes": dropped}

    @staticmethod
    def _drop_order(beat: Beat) -> List[Any]:
        """Notes of an unplayable chord, most expendable first: held, doubled, inner, top, bass."""
        ordered = sorted(beat.notes, key=lambda n: n.realValue)
        inner = ordered[1:-1]
        classes = [n.realValue % 12 for n in ordered]
        order = ([n for n in ordered if n.type == NoteType.tie]
                 + [n for n in inner if classes.count(n.realValue % 12) > 1]
                 + inner[::-1] + ordered[::-1])
        seen = set()
        return [n for n in order if id(n) not in seen and not seen.add(id(n))]

    def _voice_sequences(self, track: Track, measures: range):
        """Per voice: the beats with notes in the range, as (measure, beat, Beat, origins).

        origins[i] is the index of the note in the previous sequence entry that
        note i is tied from, "pinned" if it is tied to a note outside the
        range (so it must keep its string), or None.
        """
        voice_count = max((len(m.voices) for m in track.measures), default=0)
        for v in range(voice_count):
            flat = [(m, b, beat) for m, measure in enumerate(track.measures) if v < len(measure.voices)
                    for b, beat in enumerate(measure.voices[v].beats)]
            seq, pins = [], set()
            for k, (m, b, beat) in enumerate(flat):
                prev = flat[k - 1][2] if k else None
                in_range = m in measures
                prev_in_range = k > 0 and flat[k - 1][0] in measures
                origins = []
                for note in beat.notes:
                    origin = None
                    if note.type == NoteType.tie and prev is not None:
                        idx = next((i for i, p in enumerate(prev.notes) if p.string == note.string), None)
                        if idx is not None:
                            if in_range and prev_in_range:
                                origin = idx
                            elif in_range:
                                origin = "pinned"
                            elif prev_in_range:
                                pins.add(id(prev.notes[idx]))
                    origins.append(origin)
                if in_range and beat.notes:
                    seq.append((m, b, beat, origins))
            # Notes tied into a beat after the range keep their string too.
            seq = [(m, b, beat, ["pinned" if id(n) in pins else o for n, o in zip(beat.notes, origins)])
                   for m, b, beat, origins in seq]
            if seq:
                yield v, seq

    def _finger(self, seq, items, tuning: Dict[int, int], fret_count: int,
                max_span: int, max_jump: int):
        """Cheapest fingering for a sequence of beats (beam search over beats).

        items[i] is [(pitch, current (string, fret) or None)] per note. Tied
        notes are forced onto the string their origin was given. Returns the
        chosen assignment per beat and the indexes of beats with no valid
        fingering (those keep their current one).
        """
        layers, bad = [], []
        for i, ((_, _, beat, origins), item) in enumerate(zip(seq, items)):
            prev_layer = layers[-1] if layers else []
            linked = [(n, o) for n, o in enumerate(origins) if isinstance(o, int)]
            # Tied notes follow their origin, so group previous states by the strings they imply.
            groups: Dict[Any, List[int]] = {}
            for j, (a, _, _) in enumerate(prev_layer):
                groups.setdefault(tuple(a[o][0] for _, o in linked), []).append(j)
            if not groups:
                groups[()] = []
            current = [c for _, c in item]
            layer = []
            for key, js in groups.items():
                forced = {n: s for (n, _), s in zip(linked, key)}
                pinned = {n for n, o in enumerate(origins) if o == "pinned"}
                cands = self._candidates(item, tuning, fret_count, forced, pinned)
                cands = sorted(cands, key=lambda a: self._local_cost(a, current, max_span))[:MAX_CANDIDATES]
                for a in cands:
                    local = self._local_cost(a, current, max_span)
                    if js:
                        j = min(js, key=lambda j: prev_layer[j][1] + self._move_cost(prev_layer[j][0], a, max_jump))
                        cost = prev_layer[j][1] + self._move_cost(prev_layer[j][0], a, max_jump) + local
                    else:
                        j, cost = None, local
                    layer.append((a, cost, j))
            if not layer and linked:
                # The tied notes' strings block the rest of the chord: release the
                # ties (they become re-attacks once _repair_ties sees the new strings).
                pinned = {n for n, o in enumerate(origins) if o == "pinned"}
                for a in sorted(self._candidates(item, tuning, fret_count, {}, pinned),
                                key=lambda a: self._local_cost(a, current, max_span))[:MAX_CANDIDATES]:
                    j = (min(range(len(prev_layer)), key=lambda j: prev_layer[j][1]
                             + self._move_cost(prev_layer[j][0], a, max_jump)) if prev_layer else None)
                    base = prev_layer[j][1] + self._move_cost(prev_layer[j][0], a, max_jump) if prev_layer else 0.0
                    layer.append((a, base + self._local_cost(a, current, max_span), j))
            if not layer:
                bad.append(i)
                fallback = tuple(c if c else (0, 0) for c in current)
                j = min(range(len(prev_layer)), key=lambda j: prev_layer[j][1]) if prev_layer else None
                layer = [(fallback, prev_layer[j][1] if prev_layer else 0.0, j)]
            layers.append(sorted(layer, key=lambda x: x[1])[:MAX_CANDIDATES * 2])
        k = 0
        path = []
        for layer in reversed(layers):
            a, _, back = layer[k]
            path.append(a)
            k = back if back is not None else 0
        return path[::-1], bad

    @staticmethod
    def _candidates(item, tuning: Dict[int, int], fret_count: int,
                    forced: Dict[int, int], pinned: set):
        per_note = []
        for n, (pitch, current) in enumerate(item):
            if n in forced:
                s = forced[n]
                f = pitch - tuning.get(s, 999)
                per_note.append([(s, f)] if 0 <= f <= fret_count else [])
            elif n in pinned and current:
                per_note.append([current])
            else:
                per_note.append([(s, pitch - t) for s, t in tuning.items() if 0 <= pitch - t <= fret_count])
        # Bound the search on dense chords: keep each note's three lowest frets.
        size = 1
        for options in per_note:
            size *= max(len(options), 1)
        if size > 20000:
            per_note = [sorted(o, key=lambda sf: sf[1])[:3] for o in per_note]
        return [a for a in itertools.product(*per_note) if len({s for s, _ in a}) == len(a)]

    @staticmethod
    def _position(assignment) -> Optional[int]:
        fretted = [f for _, f in assignment if f > 0]
        return min(fretted) if fretted else None

    @staticmethod
    def _span(assignment) -> int:
        fretted = [f for _, f in assignment if f > 0]
        return max(fretted) - min(fretted) if fretted else 0

    def _local_cost(self, assignment, current, max_span: int) -> float:
        span = self._span(assignment)
        changes = sum(c is not None and a != c for a, c in zip(assignment, current))
        top = max((f for _, f in assignment), default=0)
        return 10 * max(0, span - max_span) + 0.5 * span + 1.0 * changes + 0.05 * top

    def _move_cost(self, a, b, max_jump: int) -> float:
        pa, pb = self._position(a), self._position(b)
        if pa is None or pb is None:
            return 0.0
        d = abs(pa - pb)
        return 0.3 * d + (5 if d > max_jump else 0)

    def _tally(self, counts, assignment, prev, max_span: int, max_jump: int) -> None:
        if self._span(assignment) > max_span:
            counts["wide_stretch"] += 1
        if len({s for s, _ in assignment}) != len(assignment):
            counts["string_clash"] += 1
        if prev is not None:
            pa, pb = self._position(prev), self._position(assignment)
            if pa is not None and pb is not None and abs(pa - pb) > max_jump:
                counts["position_jump"] += 1
