# guitar-pro-mcp

Python MCP server (FastMCP, stdio) that reads and edits Guitar Pro files through PyGuitarPro.

This is our fork (`origin` = tom-schof/guitar-pro-mcp, `upstream` = wegitor/guitar-pro-mcp). It supports
`~/dev/mandolin-anthem`, where we split and re-finger an AI-generated (MuScriptor) guitar
transcription so a real guitarist can play it. That project registers this server as `guitar-pro`
and runs it with:

```
uv --directory ~/dev/guitar-pro-mcp run -m src.run_mcp_server
```

## Commands

- Tests: `uv run pytest`
- Run the server: `uv run -m src.run_mcp_server`

## Layout

- `src/run_mcp_server.py`: the entry point. It points `print` at stderr, builds the controller and
  FastMCP, and calls `setup_mcp_tools`.
- `src/mcp_tools.py`: every MCP tool, each a thin wrapper around one controller method.
- `src/controllers/guitar_pro/`: `GuitarProController` plus one `*OperationsController` per
  area (file, song, track, measure, note, chord, edit, analysis). They all subclass
  `GuitarProMixin` (`base_controller.py`).
- `src/controllers/guitar_pro/edit_operations.py`: the tools we added to edit existing songs
  (`get_measures`, `find_playability_issues`, `duplicate_track`, `delete_track`, `delete_notes`,
  `edit_notes`, `add_notes`, `move_notes`, `split_track`, `merge_voices`, `make_monophonic`).
- `src/controllers/guitar_pro/analysis_operations.py`: `analyze_track` (does a track mix melody
  and chords, and where should it be split), `split_parts` (rhythm chords out, lead line stays),
  `suggest_fingering` (re-finger a passage with the same pitches, searching across neighbouring
  beats) and `retune_track` (new tuning and fret count; same pitches, re-fingered).
- `src/utils/`: MIDI and JSON export and import.
- `tests/`: pytest. The tests build a song, write it to `.gp5`, reload it and check the result.
- `references/`: local reference docs, gitignored (e.g. `references/Guitar-Pro-8-user-guide.pdf`).
  Look things up here to check how Guitar Pro 8 behaves. **Never commit anything from `references/`.**

## Architecture

- `GuitarProController.__getattr__` sends each call to the first handler that has the method.
  Before the call it copies `current_song` into that handler, and afterwards it copies it back. A new
  controller must be added in **both** places in `controllers/guitar_pro/__init__.py`: `__init__`
  and the handler list in `__getattr__`.
- Tools return `{"status": "success" | "error", ...}` and catch every exception. They never raise.
- Edit tools address a note as `{measure, voice, beat, string}`. `measure`, `voice` and `beat` are
  0-based, matching the output of `get_measures`. `string` is 1-based, as in Guitar Pro, and
  `voice` defaults to 0. Batch operations check every address before changing anything, so a
  batch either fully succeeds or changes nothing.
- Arranging a merged transcription (what mandolin-anthem does): `merge_voices` (only if two
  voices carry the part) → `split_parts` → `retune_track(reduce_chords=true)` and
  `suggest_fingering(apply=true)` on each part → save to a **new** file. **Preserve detail.** The
  user rejected a version where `make_monophonic` moved the lead's double-stops and held notes
  into a harmony track. Keep the lead's double-stops, and change pitch only by octaves when a
  note won't fit the neck. Check the result by comparing every note (including ties) with the
  source. Split by role and
  continuity, not pitch alone: taking the top note of each beat shreds arpeggios across tracks.
  That was the first attempt, and the user described the result as "gibberish". Start from a
  MIDI-based `.gp5` export (single voice, 16th grid). The MusicXML import was full of
  32nd/64th fragments. `analyze_track` and `find_playability_issues` show what to fix. For finer
  control, `split_track` can split by pitch, top note, string or explicit notes, one measure
  range at a time with `dest_track`. `split_track` creates a copy
  with the same rhythm but no notes (`duplicate_track(clear_notes=True)`) and uses `move_notes`,
  which needs a destination beat that starts at the same tick and has the same duration. Measure
  headers are shared, so all tracks stay in time.
- If two notes clash on one string, an address can add `"fret"` to pick between them.

## PyGuitarPro gotchas

- `.gp5` is the only tested format. Guitar Pro 8 can import and export it.
- A note needs `type=NoteType.normal`. The default is `rest`, and Guitar Pro doesn't show those notes.
- A beat with no notes should be `BeatStatus.rest`, and a beat with notes should be `BeatStatus.normal`.
  After changing a beat's notes, call `_refresh_status`.
- `Note.value` is the fret, and `Note.realValue` is the MIDI pitch (tuning + fret).
- Many upstream methods used attributes PyGuitarPro doesn't have (`isTiedNote`, `duration.isRest`,
  `effect.isSlide`, `song.repeatGroups`, and others). On these unslotted attrs classes, setting a
  nonexistent attribute succeeds silently and is never saved. Check `guitarpro/models.py` before
  using a field, and test by saving and reloading.
- A new measure has no beats. `_fill_rests` gives voice 0 one rest per time-signature beat, so
  `add_notes` has beats to address.
- Tie notes (`NoteType.tie`) need a note on the same string and fret in the previous beat.
  Deleting, moving or re-fingering notes calls `_repair_ties`, which turns stranded ties into
  normal notes. The fingering search moves a tie chain onto one string as a whole, and only
  releases a tie when the chord can't be fingered otherwise.
- **Two notes on one string in a beat can't be saved.** GP5 stores a beat's notes as a string
  bitmask, so the writer produces a corrupt file without raising. `save_file` refuses such songs
  (`_check_writable`). `merge_voices` can create these clashes; `retune_track` or
  `suggest_fingering` resolve them. When two notes clash, add `"fret"` to the address.
- `attrs` models compare by value, so `list.remove`, `in` and `.index` on beats and notes can
  match the wrong object. Use identity (`is`).
- `save_file` writes to a temp file and then renames it. Keep it that way, because a failed
  PyGuitarPro write leaves a partial file.

## Rules

- **Never write to stdout in server code**, because stdout carries the MCP stdio protocol.
  Log with `logging` or write to stderr.
- Test changes by writing a real `.gp5` file and reloading it (see `roundtrip` in
  `tests/test_edit_operations.py`). Checking only the in-memory objects isn't enough.
- Keep upstream mergeable. Make small, focused changes and don't do drive-by reformatting.
- Stage files explicitly by path. Don't use `git add -A` or `git add .`.
- "Commit" means commit and push. Don't add `Co-Authored-By` lines.

## Known issues and likely next work

- `set_time_signature` doesn't change existing beats, so a measure can end up overfull.
- JSON export and import only carry notes, tracks, metadata and time signatures. Lyrics, markers,
  repeats and chords are lost.
- `add_note_with_effects` works but isn't exposed as a tool. No tool changes the effects on
  existing notes, and no tool changes rhythm.
- The melody, harmony and rhythm thresholds in `split_parts` (B4, a fourth, E4, a sixth) and the
  fingering costs were tuned on one song (mandolin-anthem). Check them by ear and eye in Guitar
  Pro, and keep them as parameters.
- New tracks are appended at the end. No tool reorders tracks.
