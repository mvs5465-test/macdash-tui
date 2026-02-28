# macdash-tui

`macdash-tui` is a small terminal dashboard for macOS. Run it, get a live full-screen overview, and press `Ctrl-C` or `q` to drop back to your shell.

## What it shows

- CPU usage (user, system, idle)
- Memory usage (used, app, wired, compressed, free)
- GPU summary (device name plus a note about utilization support)
- Network throughput (receive/transmit totals and live rates)

## Why Python

This first version uses only the Python standard library, so it runs out of the box on a stock macOS install with no extra dependencies.

## Run It

```bash
python3 macdash.py
```

## Notes

- The dashboard uses `curses`, so it expects a normal interactive terminal.
- GPU utilization is intentionally marked as unavailable in this version. The script still reports the detected GPU adapter name when it can.
- Network throughput is calculated from byte deltas on the default route interface.

## Planned Improvements

- Per-core CPU bars
- Better GPU metrics when a reliable source is available
- Process and port drill-down views
- Configurable refresh interval
