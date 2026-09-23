import os
import sys

import guitarpro as gp
import pytest
from guitarpro.models import Beat, BeatStatus, Duration, Note, NoteType

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from controllers import GuitarProController  # noqa: E402

# Open C chord on strings 5-2 (C3 E3 G3 C4) and a melody on string 1.
CHORD = [(5, 3), (4, 2), (3, 0), (2, 1)]


def build_song(path):
    """Four 4/4 measures of quarter notes, strummed C chord with a melody on top.

    Beats 0 and 2 hold the chord plus a melody note (layered); beats 1 and 3
    hold single melody notes between the chords.
    """
    song = gp.models.Song()
    track = song.tracks[0]
    track.name = "Merged"
    for _ in range(3):
        song.newMeasure()
    melody = [12, 10, 8, 7]
    for measure in track.measures:
        voice = measure.voices[0]
        voice.beats = [Beat(voice, duration=Duration(value=4), status=BeatStatus.normal) for _ in range(4)]
        for i, beat in enumerate(voice.beats):
            notes = (CHORD if i % 2 == 0 else []) + [(1, melody[i])]
            beat.notes = [Note(beat, value=f, string=s, type=NoteType.normal) for s, f in notes]
    gp.write(song, path)


@pytest.fixture
def ctl(tmp_path):
    path = str(tmp_path / "merged.gp5")
    build_song(path)
    c = GuitarProController()
    c.load_file(path)
    c.tmp = tmp_path
    return c


def roundtrip(c):
    path = str(c.tmp / "out.gp5")
    c.save_file(path)
    c.load_file(path)


def notes_at(c, track, measure, beat):
    b = c.current_song.tracks[track].measures[measure].voices[0].beats[beat]
    return sorted((n.string, n.value) for n in b.notes)


def test_analyze_detects_melody_over_chords(ctl):
    a = ctl.analyze_track(0)
    assert a["should_split"]
    assert a["beat_kinds"]["layered"] == 8
    assert a["melody_notes_between_chords"] == 8
    assert {s["texture"] for s in a["sections"]} == {"melody+chords"}
    # Chord top is C4 (60); the lowest melody note is B4 (71).
    assert 60 < a["suggested_split_pitch"] <= 71
    assert a["suggestion"]["mode"] == "pitch"


def test_split_by_suggested_pitch(ctl):
    pitch = ctl.analyze_track(0)["suggested_split_pitch"]
    result = ctl.split_track(0, "pitch", name="Melody", split_pitch=pitch)
    ctl.set_track_properties(0, name="Chords")
    roundtrip(ctl)
    lead = result["track_index"]
    assert result["moved"] == 16
    assert [t.name for t in ctl.current_song.tracks] == ["Chords", "Melody"]
    assert notes_at(ctl, 0, 0, 0) == sorted(CHORD)
    assert notes_at(ctl, 0, 0, 1) == []
    assert notes_at(ctl, lead, 0, 0) == [(1, 12)]
    assert notes_at(ctl, lead, 0, 1) == [(1, 10)]
    assert not ctl.analyze_track(0)["should_split"]


def test_split_top_note_per_section_into_same_track(ctl):
    r = ctl.split_track(0, "top_note", name="Melody", end_measure=1, min_gap=5, split_pitch=65)
    ctl.split_track(0, "strings", dest_track=r["track_index"], start_measure=2, strings=[1])
    roundtrip(ctl)
    assert all(notes_at(ctl, 0, m, b) in (sorted(CHORD), []) for m in range(4) for b in range(4))
    assert notes_at(ctl, 1, 3, 3) == [(1, 7)]


def test_split_changes_nothing_on_error(ctl):
    with pytest.raises(ValueError):
        ctl.split_track(0, "notes", notes=[{"measure": 0, "beat": 0, "string": 1},
                                           {"measure": 0, "beat": 1, "string": 6}])
    with pytest.raises(ValueError, match="No notes matched"):
        ctl.split_track(0, "pitch", split_pitch=100)
    assert len(ctl.current_song.tracks) == 1
    assert notes_at(ctl, 0, 0, 0) == sorted(CHORD + [(1, 12)])


def test_moving_a_tie_origin_repairs_the_tie(ctl):
    beats = ctl.current_song.tracks[0].measures[0].voices[0].beats
    tied = next(n for n in beats[1].notes if n.string == 1)
    beats[0].notes[-1].value = tied.value  # melody holds fret 10 into beat 1
    tied.type = NoteType.tie
    ctl.split_track(0, "notes", notes=[{"measure": 0, "beat": 0, "string": 1}])
    assert tied.type == NoteType.normal
    roundtrip(ctl)
    assert notes_at(ctl, 0, 0, 1) == [(1, 10)]


def test_suggest_fingering_fixes_stretch_and_clash(ctl):
    beats = ctl.current_song.tracks[0].measures[0].voices[0].beats
    beats[0].notes = []
    beats[2].notes = [n for n in beats[2].notes if n.string != 1]  # playable open C
    # Beat 1: A2 on string 6 fret 5 with A4 on string 3 fret 14 (span 9).
    beats[1].notes = [Note(beats[1], value=5, string=6, type=NoteType.normal),
                      Note(beats[1], value=14, string=3, type=NoteType.normal)]
    # Beat 3: C4 and E4 both on string 2.
    beats[3].notes = [Note(beats[3], value=1, string=2, type=NoteType.normal),
                      Note(beats[3], value=5, string=2, type=NoteType.normal)]
    result = ctl.suggest_fingering(0, 0, 0)
    assert result["before"]["wide_stretch"] == 1 and result["before"]["string_clash"] == 1
    assert result["after"] == {"wide_stretch": 0, "string_clash": 0, "position_jump": 0}
    pitches = {b: sorted(n.realValue for n in beats[b].notes) for b in (1, 3)}
    ctl.suggest_fingering(0, 0, 0, apply=True)
    roundtrip(ctl)
    track = ctl.current_song.tracks[0]
    for b in (1, 3):
        beat = track.measures[0].voices[0].beats[b]
        assert sorted(n.realValue for n in beat.notes) == pitches[b]
        assert len({n.string for n in beat.notes}) == len(beat.notes)
    assert not [i for i in ctl.find_playability_issues(0) if i["measure"] == 0]
    # Playable beats are left alone.
    assert notes_at(ctl, 0, 0, 2) == sorted(CHORD)


def test_suggest_fingering_reports_unplayable_beats(ctl):
    beat = ctl.current_song.tracks[0].measures[1].voices[0].beats[0]
    beat.notes += [Note(beat, value=f, string=s, type=NoteType.normal) for s, f in [(6, 3), (6, 8), (3, 4)]]
    result = ctl.suggest_fingering(0, 1, 1)
    assert result["unplayable_beats"] == [{"measure": 1, "voice": 0, "beat": 0}]


def two_voice_measure(ctl):
    """Measure 0: voice 0 holds a half-note C chord; voice 1 plays four eighths then a half."""
    measure = ctl.current_song.tracks[0].measures[0]
    v0, v1 = measure.voices
    v0.beats = [Beat(v0, duration=Duration(value=2), status=BeatStatus.normal),
                Beat(v0, duration=Duration(value=2), status=BeatStatus.rest)]
    v0.beats[0].notes = [Note(v0.beats[0], value=f, string=s, type=NoteType.normal) for s, f in CHORD]
    v1.beats = [Beat(v1, duration=Duration(value=8), status=BeatStatus.normal) for _ in range(4)]
    v1.beats.append(Beat(v1, duration=Duration(value=2), status=BeatStatus.normal))
    for beat, fret in zip(v1.beats, [12, 10, 8, 7, 5]):
        beat.notes = [Note(beat, value=fret, string=1, type=NoteType.normal)]


def test_merge_voices_recuts_rhythm_with_ties(ctl):
    two_voice_measure(ctl)
    assert ctl.merge_voices(0, 0, 0) == {"merged_measures": 1, "skipped_measures": []}
    roundtrip(ctl)
    measure = ctl.current_song.tracks[0].measures[0]
    beats = measure.voices[0].beats
    assert [b.duration.value for b in beats] == [8, 8, 8, 8, 2]
    assert sorted((n.string, n.value, n.type.name) for n in beats[1].notes) == \
        [(1, 10, "normal")] + [(s, f, "tie") for s, f in sorted(CHORD)]
    assert [n.value for n in beats[4].notes] == [5]
    assert not any(b.notes for b in measure.voices[1].beats)


def test_retune_seven_string_to_six_with_octave_drop(ctl):
    track = ctl.current_song.tracks[0]
    track.strings.append(gp.models.GuitarString(7, 35))
    track.fretCount = 30
    beats = track.measures[1].voices[0].beats
    beats[0].notes = [Note(beats[0], value=5, string=7, type=NoteType.normal)]    # E2 on string 7
    beats[1].notes = [Note(beats[1], value=5, string=7, type=NoteType.tie)]      # tied on
    beats[2].notes = [Note(beats[2], value=28, string=1, type=NoteType.normal)]  # G#6: above fret 24
    result = ctl.retune_track(0, fret_count=24)
    roundtrip(ctl)
    track = ctl.current_song.tracks[0]
    assert len(track.strings) == 6 and track.fretCount == 24
    assert result["octave_shifted_notes"] == 1
    beats = track.measures[1].voices[0].beats
    assert [(n.string, n.value, n.type) for n in beats[0].notes + beats[1].notes] == \
        [(6, 0, NoteType.normal), (6, 0, NoteType.tie)]
    assert beats[2].notes[0].realValue == 64 + 28 - 12
    # Everything else kept its pitch and fits the new neck.
    assert all(0 <= n.value <= 24 and n.string <= 6 for m in track.measures
               for v in m.voices for b in v.beats for n in b.notes)
    # The open C with melody at fret 12 (an 11-fret stretch) is re-voiced, same pitches.
    chord = track.measures[0].voices[0].beats[0].notes
    assert sorted(n.realValue for n in chord) == [48, 52, 55, 60, 76]
    assert ctl.suggest_fingering(0)["after"]["wide_stretch"] == 0


def test_retune_refuses_too_many_notes(ctl):
    beat = ctl.current_song.tracks[0].measures[2].voices[0].beats[1]
    beat.notes = [Note(beat, value=f, string=s, type=NoteType.normal)
                  for s, f in [(1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (6, 0), (6, 2)]]
    with pytest.raises(ValueError, match="can't be fingered"):
        ctl.retune_track(0)
    assert notes_at(ctl, 0, 2, 1)[-1] == (6, 2)


def test_split_parts_melody_harmony_rhythm(ctl):
    beat = ctl.current_song.tracks[0].measures[1].voices[0].beats[0]
    beat.notes.append(Note(beat, value=9, string=2, type=NoteType.normal))  # G#4 under the melody
    beat.notes = [n for n in beat.notes if n.string != 2 or n.value == 9]
    result = ctl.split_parts(0, melody_name="Melody")
    roundtrip(ctl)
    names = [t.name for t in ctl.current_song.tracks]
    assert names == ["Melody", "Merged (Rhythm)", "Merged (Harmony)"]
    assert result["moved"] == {"rhythm": 31, "harmony": 1}
    assert notes_at(ctl, 0, 1, 0) == [(1, 12)]
    assert notes_at(ctl, 0, 1, 1) == [(1, 10)]
    assert notes_at(ctl, 2, 1, 0) == [(2, 9)]
    assert notes_at(ctl, 1, 0, 0) == sorted(CHORD)


def test_make_monophonic_cuts_held_and_moves_extra_notes(ctl):
    beats = ctl.current_song.tracks[0].measures[0].voices[0].beats
    for beat in beats:
        beat.notes = [n for n in beat.notes if n.string == 1]
    # Beat 1: melody note held from beat 0 (tie) plus a new higher note.
    beats[1].notes = [Note(beats[1], value=12, string=1, type=NoteType.tie),
                      Note(beats[1], value=5, string=2, type=NoteType.normal),
                      Note(beats[1], value=15, string=3, type=NoteType.normal)]
    other = ctl.duplicate_track(0, "Harmony", clear_notes=True)
    result = ctl.make_monophonic(0, dest_track=other, end_measure=0)
    assert result == {"moved": 1, "deleted": 0, "cut": 1}
    roundtrip(ctl)
    assert [n.realValue for n in ctl.current_song.tracks[0].measures[0].voices[0].beats[1].notes] == [70]
    assert [n.realValue for n in ctl.current_song.tracks[other].measures[0].voices[0].beats[1].notes] == [64]


def test_reduce_chords_drops_only_the_blocking_note(ctl):
    beat = ctl.current_song.tracks[0].measures[2].voices[0].beats[0]
    # C6 and D#6 both need string 1 on a 24-fret guitar.
    beat.notes = [Note(beat, value=f, string=s, type=NoteType.normal)
                  for s, f in [(5, 3), (3, 0), (1, 20), (2, 28)]]
    ctl.current_song.tracks[0].fretCount = 30
    result = ctl.retune_track(0, reduce_chords=True)
    assert [d["pitch"] for d in result["dropped_notes"]] == ["C6"]
    kept = ctl.current_song.tracks[0].measures[2].voices[0].beats[0].notes
    assert sorted(n.realValue for n in kept) == [48, 55, 87]


def test_save_refuses_string_clash(ctl):
    beat = ctl.current_song.tracks[0].measures[0].voices[0].beats[1]
    beat.notes.append(Note(beat, value=3, string=1, type=NoteType.normal))
    with pytest.raises(ValueError, match="two notes on one string"):
        ctl.save_file(str(ctl.tmp / "bad.gp5"))
    assert not (ctl.tmp / "bad.gp5").exists()
