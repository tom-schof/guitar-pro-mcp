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
  `edit_notes`, `add_notes`, `move_notes`, `split_track`).
- `src/controllers/guitar_pro/analysis_operations.py`: `analyze_track` (does a track mix melody
  and chords, and where should it be split) and `suggest_fingering` (re-finger a passage with the
  same pitches, searching across neighbouring beats).
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
- Workflow for a merged part: run `analyze_track`, then `split_track` using the suggested mode
  and pitch (run it per measure range with `dest_track` if sections differ), then
  `find_playability_issues` and `suggest_fingering` on each part. `split_track` creates a copy
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
- Tie notes (`NoteType.tie`) need a note on the same string in the previous beat. Deleting or
  moving notes calls `_repair_ties`.
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
- The `analyze_track` and `suggest_fingering` heuristics are only tested on synthetic songs.
  Tune them against the real mandolin-anthem transcription.
