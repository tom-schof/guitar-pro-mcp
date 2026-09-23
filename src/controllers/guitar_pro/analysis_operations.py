import itertools
import statistics
from typing import Any, Dict, List, Optional, Tuple

from guitarpro.models import Beat, NoteType, Track

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
        stay put). Tied and dead notes keep their string. With apply=True the
        edits are made through edit_notes.
        """
        track = self._track(track_index)
        measures = self._range(track, start_measure, end_measure)
        tuning = {s.number: s.value for s in track.strings}
        edits: List[Dict[str, Any]] = []
        unplayable: List[Dict[str, int]] = []
        before = {"wide_stretch": 0, "string_clash": 0, "position_jump": 0}
        after = dict(before)

        voice_count = max((len(track.measures[m].voices) for m in measures), default=0)
        for v in range(voice_count):
            seq = [(m, b, beat) for m in measures if v < len(track.measures[m].voices)
                   for b, beat in enumerate(track.measures[m].voices[v].beats) if beat.notes]
            if not seq:
                continue
            fixed = self._tied_notes(track, v)
            options = []
            for m, b, beat in seq:
                current = tuple((n.string, n.value) for n in beat.notes)
                cands = self._candidates(beat, tuning, track.fretCount, fixed)
                if not cands:
                    unplayable.append({"measure": m, "voice": v, "beat": b})
                    cands = [current]
                options.append((current, sorted(
                    cands, key=lambda a: self._local_cost(a, current, max_fret_span))[:MAX_CANDIDATES]))

            path = self._best_path(options, max_fret_span, max_position_jump)
            prev_cur = prev_new = None
            for (m, b, beat), (current, _), chosen in zip(seq, options, path):
                self._tally(before, current, prev_cur, max_fret_span, max_position_jump)
                self._tally(after, chosen, prev_new, max_fret_span, max_position_jump)
                prev_cur, prev_new = current, chosen
                for note, (s, f) in zip(beat.notes, chosen):
                    if (s, f) != (note.string, note.value):
                        edits.append({"measure": m, "voice": v, "beat": b, "string": note.string,
                                      "fret": note.value, "new_string": s, "new_fret": f})
        if apply and edits:
            self.edit_notes(track_index, edits)
        return {"edits": edits, "changed_notes": len(edits), "applied": bool(apply and edits),
                "before": before, "after": after, "unplayable_beats": unplayable}

    @staticmethod
    def _tied_notes(track: Track, voice: int) -> set:
        """ids of notes that are tied or tied into, which must keep their string."""
        fixed, prev = set(), None
        for measure in track.measures:
            if voice >= len(measure.voices):
                continue
            for beat in measure.voices[voice].beats:
                for note in beat.notes:
                    if note.type == NoteType.tie:
                        fixed.add(id(note))
                        if prev:
                            fixed.update(id(p) for p in prev.notes if p.string == note.string)
                prev = beat
        return fixed

    @staticmethod
    def _candidates(beat: Beat, tuning: Dict[int, int], fret_count: int, fixed: set):
        per_note = []
        for note in beat.notes:
            if id(note) in fixed or note.type == NoteType.dead or note.string not in tuning:
                per_note.append([(note.string, note.value)])
            else:
                pitch = note.realValue
                per_note.append([(s, pitch - t) for s, t in tuning.items() if 0 <= pitch - t <= fret_count])
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
        changes = sum(a != c for a, c in zip(assignment, current))
        top = max((f for _, f in assignment), default=0)
        return 10 * max(0, span - max_span) + 0.5 * span + 1.0 * changes + 0.05 * top

    def _move_cost(self, a, b, max_jump: int) -> float:
        pa, pb = self._position(a), self._position(b)
        if pa is None or pb is None:
            return 0.0
        d = abs(pa - pb)
        return 0.3 * d + (5 if d > max_jump else 0)

    def _best_path(self, options, max_span: int, max_jump: int):
        """Cheapest sequence of fingerings (Viterbi over beats)."""
        current, cands = options[0]
        costs = [self._local_cost(a, current, max_span) for a in cands]
        back = []
        for current, cands_next in options[1:]:
            new_costs, pointers = [], []
            for a in cands_next:
                local = self._local_cost(a, current, max_span)
                i = min(range(len(cands)), key=lambda j: costs[j] + self._move_cost(cands[j], a, max_jump))
                new_costs.append(costs[i] + self._move_cost(cands[i], a, max_jump) + local)
                pointers.append(i)
            back.append((cands, pointers))
            cands, costs = cands_next, new_costs
        i = min(range(len(cands)), key=costs.__getitem__)
        path = [cands[i]]
        for prev_cands, pointers in reversed(back):
            i = pointers[i]
            path.append(prev_cands[i])
        return path[::-1]

    def _tally(self, counts, assignment, prev, max_span: int, max_jump: int) -> None:
        if self._span(assignment) > max_span:
            counts["wide_stretch"] += 1
        if len({s for s, _ in assignment}) != len(assignment):
            counts["string_clash"] += 1
        if prev is not None:
            pa, pb = self._position(prev), self._position(assignment)
            if pa is not None and pb is not None and abs(pa - pb) > max_jump:
                counts["position_jump"] += 1
