# AGENTS.md

Instructions for human + AI contributors in this repository.

## Product

- `macdash-tui` is a small macOS terminal dashboard for live system stats.
- The current app is intentionally minimal and standard-library-first.

## Architecture

- `macdash.py` contains the whole application: metrics collection, curses layout, and refresh loop.
- `assets/` holds repo docs assets such as the screenshot.

## Working Rules

- Preserve the no-dependency local run path unless there is a compelling reason to change it.
- Keep the app usable in a normal interactive terminal first; avoid complexity that hurts the TUI loop.
- Prefer clear, compact terminal presentation over excessive configuration.

## Verification

- Run `python3 macdash.py` in a real terminal.
- Verify quitting, redraw behavior, and basic stats rendering after UI changes.
