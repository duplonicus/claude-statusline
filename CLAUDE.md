# claude-statusline

Status line for Claude Code. `src/statusline.py` is the whole program: stdlib only, reads the session JSON on stdin, prints two rows.

- Tests: `uvx pytest` (must stay green; `test_full_render_wide` pins the exact output).
- Preview: `python3 scripts/demo.py [columns]`.
- README screenshot: `uv run --with playwright scripts/screenshot.py`, after any visible change.
- This checkout is the live status line on dup's machine (`~/.claude/settings.json` points at it), so a broken commit shows up in every session immediately.
- No dependencies in `src/`. It runs on every message and has to start fast.
