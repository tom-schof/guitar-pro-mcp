"""Round-trip tests for the pre-existing (upstream) tools."""
import os
import sys

import guitarpro as gp
import pytest
from guitarpro.models import BeatStatus, KeySignature, NoteType, SlideType

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from controllers import GuitarProController  # noqa: E402


@pytest.fixture
def ctl(tmp_path):
    c = GuitarProController()
    c.create_new_song("Test", "Me")
    c.tmp = tmp_path
    return c


def roundtrip(c, name="out.gp5"):
    path = str(c.tmp / name)
    c.save_file(path)
    c.load_file(path)


def beats(c, track, measure, voice=0):
    return c.current_song.tracks[track].measures[measure].voices[voice].beats


def test_new_song_and_tracks_have_addressable_rest_beats(ctl):
    t = ctl.add_track("Second")
    ctl.add_measure_header()
    roundtrip(ctl)
    song = ctl.current_song
    assert [len(tr.strings) for tr in song.tracks] == [6, 6]
    assert song.tracks[0].channel.channel != song.tracks[t].channel.channel
    for track in (0, t):
        for measure in (0, 1):
            b = beats(ctl, track, measure)
            assert [x.status for x in b] == [BeatStatus.rest] * 4
    ctl.add_notes(t, [{"measure": 1, "beat": 2, "string": 3, "fret": 2}])
    roundtrip(ctl)
    assert [(n.string, n.value) for n in beats(ctl, t, 1)[2].notes] == [(3, 2)]


def test_add_measure_copies_time_signature(ctl):
    ctl.set_time_signature(0, 3, 4)
    ctl.add_measure_header()
    roundtrip(ctl)
    ts = ctl.current_song.measureHeaders[1].timeSignature
    assert (ts.numerator, ts.denominator.value) == (3, 4)
    assert len(beats(ctl, 0, 1)) == 3


def test_add_note_with_effects_survives_roundtrip(ctl):
    ctl.add_note_with_effects(0, 0, 3, 7, beat_index=0, is_bend=True, is_vibrato=True)
    ctl.add_note_with_effects(0, 0, 2, 5, beat_index=1, is_slide=True, is_hammer_on=True)
    ctl.add_note_with_effects(0, 0, 1, 12, beat_index=2, is_harmonic=True, is_ghost=True)
    ctl.add_note_with_effects(0, 0, 6, 0, beat_index=3, is_dead=True)
    roundtrip(ctl)
    bend, slide, harm, dead = (b.notes[0] for b in beats(ctl, 0, 0))
    assert bend.effect.isBend and bend.effect.bend.points[-1].value == 4 and bend.effect.vibrato
    assert slide.effect.slides == [SlideType.shiftSlideTo] and slide.effect.hammer
    assert harm.effect.isHarmonic and harm.effect.ghostNote
    assert dead.type == NoteType.dead


def test_get_track_notes_range(ctl):
    ctl.add_measure_header()
    ctl.add_notes(0, [{"measure": 0, "beat": 0, "string": 1, "fret": 3},
                      {"measure": 1, "beat": 1, "string": 2, "fret": 1}])
    notes = ctl.get_track_notes(0, 1, 1)
    assert [(n["measure"], n["beat"], n["pitch"], n["type"]) for n in notes] == [(1, 1, 60, "normal")]
    assert len(ctl.get_track_notes(0)) == 2


def test_get_tracks_includes_percussion(ctl):
    t = ctl.add_track("Drums")
    ctl.current_song.tracks[t].isPercussionTrack = True
    assert [x["is_percussion"] for x in ctl.get_tracks()] == [False, True]


def test_transpose_is_all_or_nothing(ctl):
    ctl.add_notes(0, [{"measure": 0, "beat": 0, "string": 1, "fret": 1},
                      {"measure": 0, "beat": 1, "string": 2, "fret": 5}])
    with pytest.raises(ValueError, match="outside frets"):
        ctl.transpose_track(0, -2)
    assert [n.value for b in beats(ctl, 0, 0) for n in b.notes] == [1, 5]
    ctl.transpose_track(0, 2)
    roundtrip(ctl)
    assert [n.value for b in beats(ctl, 0, 0) for n in b.notes] == [3, 7]


def test_song_structure_tools_roundtrip(ctl):
    for _ in range(3):
        ctl.add_measure_header()
    ctl.set_key_signature(0, 2)
    ctl.set_lyrics("hello world")
    ctl.add_repeat_group(0, 1, repeat_count=3)
    ctl.add_section(0, 1, "Intro")
    ctl.add_section(2, 3, "Verse")
    ctl.add_double_bar(1)
    roundtrip(ctl)
    assert ctl.current_song.measureHeaders[0].keySignature == KeySignature.DMajor
    assert ctl.get_lyrics() == "hello world"
    assert ctl.get_repeat_groups() == [{"type": "normal", "repeat_count": 3, "endings": [],
                                        "measures": [0, 1], "is_closed": True}]
    assert ctl.get_sections() == [{"name": "Intro", "start_measure": 0, "end_measure": 1},
                                  {"name": "Verse", "start_measure": 2, "end_measure": 3}]
    markers = ctl.get_song_structure()["markers"]
    assert {"type": "double_bar", "measure": 1} in markers
    assert sum(m["type"] == "marker" for m in markers) == 2


def test_chord_roundtrip(ctl):
    ctl.add_chord(0, 0, 0, {"name": "C", "root": "C", "type": "major",
                            "strings": [0, 1, 0, 2, 3, -1], "fingerings": [-1, 1, -1, 2, 3, -1]})
    roundtrip(ctl)
    chord = ctl.get_chord(0, 0, 0)
    assert chord["name"] == "C" and chord["strings"][:6] == [0, 1, 0, 2, 3, -1]


def test_json_roundtrip(ctl):
    ctl.add_track("Lead")
    ctl.add_notes(1, [{"measure": 0, "beat": 1, "string": 1, "fret": 12}])
    ctl.add_notes(0, [{"measure": 0, "beat": 0, "string": 5, "fret": 3}])
    before = [ctl.get_track_notes(t) for t in (0, 1)]
    path = str(ctl.tmp / "song.json")
    assert ctl.export_to_json(path)
    assert ctl.import_from_json(path)
    roundtrip(ctl)
    assert [ctl.get_track_notes(t) for t in (0, 1)] == before


def test_failed_save_keeps_existing_file(ctl, monkeypatch):
    path = ctl.tmp / "keep.gp5"
    ctl.save_file(str(path))
    original = path.read_bytes()
    import controllers.guitar_pro.file_operations as fo

    def boom(song, stream):
        open(stream, "wb").write(b"partial")
        raise RuntimeError("disk full")
    monkeypatch.setattr(fo, "write", boom)
    with pytest.raises(RuntimeError):
        ctl.save_file(str(path))
    assert path.read_bytes() == original
    assert os.listdir(ctl.tmp) == ["keep.gp5"]


def test_changing_instrument_clears_amp_preset(ctl):
    track = ctl.current_song.tracks[0]
    track.rse.instrument.effect = "British Vintage - Screamer Overdrive"
    track.rse.instrument.effectCategory = "Amp Tones"
    ctl.set_track_properties(0, instrument=27)
    roundtrip(ctl)
    track = ctl.current_song.tracks[0]
    assert track.channel.instrument == 27
    assert (track.rse.instrument.instrument, track.rse.instrument.effect) == (27, "")
