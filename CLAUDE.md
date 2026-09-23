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
  area (file, song, track, measure, note, edit). They all subclass `GuitarProMixin` (`base_controller.py`).
- `src/controllers/guitar_pro/edit_operations.py`: the tools we added to edit existing songs
  (`get_measures`, `find_playability_issues`, `duplicate_track`, `delete_track`, `delete_notes`,
  `edit_notes`, `add_notes`, `move_notes`).
- `src/utils/`: MIDI and JSON export and import.
- `tests/`: pytest. The tests build a song, write it to `.gp5`, reload it and check the result.
- `references/`: local reference docs, gitignored (e.g. `references/Guitar-Pro-8-user-guide.pdf`).
  Look things up here to check how Guitar Pro 8 behaves. **Never commit anything from `references/`.**

## Architecture

- `GuitarProController.__getattr__` sends each call to the first handler that has the method.
  Before the call it copies `current_song` into that handler, and afterwards it copies it back. A new
  controller must be added in **both** places in `controllers/guitar_pro/__init__.py`: `__init__`
  and the handler list in `__getattr__`. (`chord_operations.py` is missing from both, so
  the `add_chord` and `get_chord` tools currently always return an error.)
- Tools return `{"status": "success" | "error", ...}` and catch every exception. They never raise.
- Edit tools address a note as `{measure, voice, beat, string}`. `measure`, `voice` and `beat` are
  0-based, matching the output of `get_measures`. `string` is 1-based, as in Guitar Pro, and
  `voice` defaults to 0. Batch operations check every address before changing anything, so a
  batch either fully succeeds or changes nothing.
- To split a merged part, run `duplicate_track`, then `delete_notes` on each copy. Measure headers
  stay shared, so the copies stay in time. `move_notes` needs a destination beat that starts at the
  same tick and has the same duration.

## PyGuitarPro gotchas

- `.gp5` is the only tested format. Guitar Pro 8 can import and export it.
- A note needs `type=NoteType.normal`. The default is `rest`, and Guitar Pro doesn't show those notes.
- A beat with no notes should be `BeatStatus.rest`, and a beat with notes should be `BeatStatus.normal`.
  After changing a beat's notes, call `_refresh_status`.
- `Note.value` is the fret, and `Note.realValue` is the MIDI pitch (tuning + fret).
- `get_tracks` (the `get_gp_tracks` tool) hides percussion tracks. The `index` it returns is still
  the true song index.

## Rules

- **Never write to stdout in server code**, because stdout carries the MCP stdio protocol.
  Log with `logging` or write to stderr.
- Test changes by writing a real `.gp5` file and reloading it (see `roundtrip` in
  `tests/test_edit_operations.py`). Checking only the in-memory objects isn't enough.
- Keep upstream mergeable. Make small, focused changes and don't do drive-by reformatting.
- Stage files explicitly by path. Don't use `git add -A` or `git add .`.
- "Commit" means commit and push. Don't add `Co-Authored-By` lines.

## Known issues and likely next work

- `get_track_notes` returns the whole track. Add a measure range.
- Check the older tools for the same rest/empty bugs. For example, `add_note_with_effects` sets
  `note.effect.bend = True` when it should set a `BendEffect`. No tool calls that method yet.
- Decide whether `get_tracks` should hide percussion tracks.
- Register `ChordOperationsController` or remove the chord tools.
