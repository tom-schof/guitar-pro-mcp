import os
import sys

import guitarpro as gp
import pytest
from guitarpro.models import Beat, BeatStatus, Duration, Note, NoteType

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from controllers import GuitarProController  # noqa: E402


def build_song(path):
    """One guitar track, two 4/4 measures of quarter notes.

    Measure 0 beat 0 holds a rhythm chord (strings 6,5,4) plus a lead note
    on string 1 fret 12 — two parts merged into one beat.
    """
    song = gp.models.Song()
    track = song.tracks[0]
    track.name = "Combined Guitar"
    song.newMeasure()
    for measure in track.measures:
        voice = measure.voices[0]
        voice.beats = []
        for _ in range(4):
            beat = Beat(voice, duration=Duration(value=4), status=BeatStatus.normal)
            voice.beats.append(beat)
    first = track.measures[0].voices[0].beats[0]
    for string, fret in [(6, 3), (5, 5), (4, 5), (1, 12)]:
        first.notes.append(Note(first, value=fret, string=string, type=NoteType.normal))
    second = track.measures[0].voices[0].beats[1]
    second.notes.append(Note(second, value=3, string=2, type=NoteType.normal))
    gp.write(song, path)


@pytest.fixture
def ctl(tmp_path):
    path = str(tmp_path / "combined.gp5")
    build_song(path)
    c = GuitarProController()
    c.load_file(path)
    c.tmp = tmp_path
    return c


def roundtrip(c, name="out.gp5"):
    path = str(c.tmp / name)
    c.save_file(path)
    c.load_file(path)


def beat_notes(c, track, measure, beat, voice=0):
    b = c.current_song.tracks[track].measures[measure].voices[voice].beats[beat]
    return sorted((n.string, n.value) for n in b.notes)


def test_get_measures_reports_pitch_and_ticks(ctl):
    data = ctl.get_measures(0, 0, 0)
    beats = data[0]["voices"][0]["beats"]
    assert [b["tick"] for b in beats] == [0, 960, 1920, 2880]
    lead = next(n for n in beats[0]["notes"] if n["string"] == 1)
    assert lead["pitch"] == 64 + 12
    assert beats[2]["rest"] is True


def test_split_by_duplicate_then_delete(ctl):
    lead = ctl.duplicate_track(0, "Lead Guitar")
    ctl.set_track_properties(0, name="Rhythm Guitar")
    ctl.delete_notes(0, [{"measure": 0, "beat": 0, "string": 1},
                         {"measure": 0, "beat": 1, "string": 2}])
    ctl.delete_notes(lead, [{"measure": 0, "beat": 0, "string": s} for s in (6, 5, 4)])
    roundtrip(ctl)
    tracks = ctl.current_song.tracks
    assert [t.name for t in tracks] == ["Rhythm Guitar", "Lead Guitar"]
    assert tracks[0].channel.channel != tracks[1].channel.channel
    assert beat_notes(ctl, 0, 0, 0) == [(4, 5), (5, 5), (6, 3)]
    assert beat_notes(ctl, 1, 0, 0) == [(1, 12)]
    assert beat_notes(ctl, 0, 0, 1) == []
    assert tracks[0].measures[0].voices[0].beats[1].status == BeatStatus.rest
    assert beat_notes(ctl, 1, 0, 1) == [(2, 3)]
    # Measure headers stay shared, so timing lines up across tracks.
    assert len(tracks[0].measures) == len(tracks[1].measures) == 2


def test_edit_notes_keeps_pitch(ctl):
    # string 1 fret 12 (E5) -> string 2 should be fret 17
    ctl.edit_notes(0, [{"measure": 0, "beat": 0, "string": 1, "new_string": 2}])
    roundtrip(ctl)
    assert (2, 17) in beat_notes(ctl, 0, 0, 0)


def test_edit_notes_rejects_string_clash_atomically(ctl):
    with pytest.raises(ValueError, match="two notes on one string"):
        ctl.edit_notes(0, [{"measure": 0, "beat": 0, "string": 4, "new_fret": 7},
                           {"measure": 0, "beat": 0, "string": 5, "new_string": 4}])
    assert beat_notes(ctl, 0, 0, 0) == [(1, 12), (4, 5), (5, 5), (6, 3)]


def test_move_notes_to_duplicated_track(ctl):
    lead = ctl.duplicate_track(0, "Lead Guitar")
    ctl.delete_notes(lead, [{"measure": 0, "beat": 0, "string": s} for s in (1, 4, 5, 6)])
    ctl.move_notes(0, lead, [{"measure": 0, "beat": 0, "string": 1}])
    roundtrip(ctl)
    assert beat_notes(ctl, 0, 0, 0) == [(4, 5), (5, 5), (6, 3)]
    assert beat_notes(ctl, lead, 0, 0) == [(1, 12)]


def test_add_notes_and_legacy_add_note_are_visible(ctl):
    ctl.add_notes(0, [{"measure": 1, "beat": 0, "string": 3, "fret": 2}])
    ctl.add_note(0, 1, 2, 1, beat_index=1)
    roundtrip(ctl)
    for beat, expected in [(0, (3, 2)), (1, (2, 1))]:
        b = ctl.current_song.tracks[0].measures[1].voices[0].beats[beat]
        assert b.status == BeatStatus.normal
        assert [(n.string, n.value, n.type) for n in b.notes] == [(*expected, NoteType.normal)]


def test_playability_flags_merged_parts(ctl):
    issues = ctl.find_playability_issues(0, max_fret_span=5)
    stretch = [i for i in issues if i["issue"] == "wide_stretch"]
    assert stretch and stretch[0]["span"] == 9


def test_delete_track_renumbers(ctl):
    ctl.duplicate_track(0, "A")
    ctl.duplicate_track(0, "B")
    ctl.delete_track(1)
    assert [(t.number, t.name) for t in ctl.current_song.tracks] == [(1, "Combined Guitar"), (2, "B")]
    with pytest.raises(ValueError):
        ctl.delete_track(5)
