#!/usr/bin/env python3
"""
Interactive RSSI viewer for SiK radio CSV data produced by rssi-compare.py.

Usage:
    python rssi-viewer.py rssi_radio1_<ts>.csv rssi_radio2_<ts>.csv [rssi_diff_<ts>.csv]

Controls:
    Toolbar buttons  – zoom-box, pan, home, back/forward (matplotlib built-in)
    Scroll wheel     – zoom in/out on time axis, centred on the mouse cursor
    Left-click       – place cursor A  (blue dashed)
    Right-click      – place cursor B  (green dashed)
    C                – clear both measurement cursors
    Escape           – clear cursors AND reset zoom to full view
"""

import argparse
import csv
import sys
from datetime import datetime

try:
    import matplotlib
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    from matplotlib.ticker import FuncFormatter
    from matplotlib.widgets import MultiCursor
except ImportError:
    print("Missing dependency: pip install matplotlib")
    sys.exit(1)


# ── CSV parsers ───────────────────────────────────────────────────────────────

def parse_radio_csv(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rows.append({
                    "ts":          datetime.fromisoformat(row["timestamp"]),
                    "local_rssi":  int(row["local_rssi"]),
                    "local_noise": int(row["local_noise"]),
                    "local_snr":   int(row["local_snr"]),
                    "node_id":     int(row["node_id"]),
                })
            except (KeyError, ValueError):
                continue
    return rows


def parse_diff_csv(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rows.append({
                    "ts":              datetime.fromisoformat(row["timestamp"]),
                    "local_rssi_diff": float(row["local_rssi_diff"]),
                    "local_snr_diff":  float(row["local_snr_diff"]),
                })
            except (KeyError, ValueError):
                continue
    return rows


def to_elapsed(rows: list[dict], t0: datetime, key: str = "ts") -> list[float]:
    return [(r[key] - t0).total_seconds() for r in rows]


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Interactive viewer for rssi-compare.py CSV output"
    )
    parser.add_argument("csv1", help="Radio 1 CSV  (rssi_radio1_*.csv)")
    parser.add_argument("csv2", help="Radio 2 CSV  (rssi_radio2_*.csv)")
    parser.add_argument("diff", nargs="?", help="Diff CSV  (rssi_diff_*.csv) — optional")
    args = parser.parse_args()

    # ── Load data ─────────────────────────────────────────────────────────
    print(f"Reading {args.csv1} …")
    rows1 = parse_radio_csv(args.csv1)
    print(f"Reading {args.csv2} …")
    rows2 = parse_radio_csv(args.csv2)
    rows_diff: list[dict] = []
    if args.diff:
        print(f"Reading {args.diff} …")
        rows_diff = parse_diff_csv(args.diff)

    if not rows1 and not rows2:
        print("No data found in CSVs.")
        sys.exit(1)

    all_ts = [r["ts"] for r in rows1] + [r["ts"] for r in rows2]
    t0 = min(all_ts)

    el1   = to_elapsed(rows1, t0)
    rssi1 = [r["local_rssi"]  for r in rows1]
    nse1  = [r["local_noise"] for r in rows1]
    snr1  = [r["local_snr"]   for r in rows1]

    el2   = to_elapsed(rows2, t0)
    rssi2 = [r["local_rssi"]  for r in rows2]
    nse2  = [r["local_noise"] for r in rows2]
    snr2  = [r["local_snr"]   for r in rows2]

    el_d   = to_elapsed(rows_diff, t0) if rows_diff else []
    d_rssi = [r["local_rssi_diff"] for r in rows_diff]
    d_snr  = [r["local_snr_diff"]  for r in rows_diff]

    all_x = el1 + el2 + el_d
    x_min = min(all_x) if all_x else 0.0
    x_max = max(all_x) if all_x else 1.0
    x_margin = (x_max - x_min) * 0.02 or 1.0

    # ── Figure layout (mirrors rssi-compare.py) ───────────────────────────
    fig = plt.figure(figsize=(18, 13))
    fig.suptitle(
        f"RSSI Viewer  —  {args.csv1}  |  {args.csv2}",
        fontsize=11,
    )
    gs = gridspec.GridSpec(5, 2, figure=fig, hspace=0.35, wspace=0.28)

    ax_diff     = fig.add_subplot(gs[0, :])
    ax_snr_diff = fig.add_subplot(gs[1, :],   sharex=ax_diff)
    ax1_rssi    = fig.add_subplot(gs[2, 0],   sharex=ax_diff)
    ax1_noise   = fig.add_subplot(gs[3, 0],   sharex=ax_diff)
    ax1_snr     = fig.add_subplot(gs[4, 0],   sharex=ax_diff)
    ax2_rssi    = fig.add_subplot(gs[2, 1],   sharex=ax_diff)
    ax2_noise   = fig.add_subplot(gs[3, 1],   sharex=ax_diff)
    ax2_snr     = fig.add_subplot(gs[4, 1],   sharex=ax_diff)

    all_axes = [
        ax_diff, ax_snr_diff,
        ax1_rssi, ax1_noise, ax1_snr,
        ax2_rssi, ax2_noise, ax2_snr,
    ]

    C1 = "#1f77b4"   # blue   – radio 1
    C2 = "#ff7f0e"   # orange – radio 2

    def fmt_time(x, _):
        return f"{int(x)}s"

    # ── Plot data ─────────────────────────────────────────────────────────
    if el_d:
        ax_diff.plot(el_d, d_rssi, color="purple", lw=1.8)
        ax_snr_diff.plot(el_d, d_snr, color="#2ca02c", lw=1.8)

    ax_diff.axhline(0, color="grey", lw=0.8, ls="--")
    ax_diff.set_ylabel("RSSI diff")
    ax_diff.set_ylim(-128, 128)
    ax_diff.set_title("Local RSSI Difference  (Radio 1 − Radio 2)")
    ax_diff.grid(True, alpha=0.3)
    ax_diff.xaxis.set_major_formatter(FuncFormatter(fmt_time))

    ax_snr_diff.axhline(0, color="grey", lw=0.8, ls="--")
    ax_snr_diff.set_ylabel("SNR diff")
    ax_snr_diff.set_ylim(-128, 128)
    ax_snr_diff.set_title("Local SNR Difference  (Radio 1 − Radio 2)")
    ax_snr_diff.grid(True, alpha=0.3)
    ax_snr_diff.xaxis.set_major_formatter(FuncFormatter(fmt_time))

    ax1_rssi.plot(el1, rssi1, color=C1, lw=1.5)
    ax1_rssi.set_ylabel("RSSI (raw, 0–255)")
    ax1_rssi.set_ylim(100, 300)
    ax1_rssi.set_title("Radio 1 – Signal Strength")
    ax1_rssi.grid(True, alpha=0.3)

    ax1_noise.plot(el1, nse1, color=C1, lw=1.5)
    ax1_noise.set_ylabel("Noise Floor (raw)")
    ax1_noise.set_ylim(0, 150)
    ax1_noise.set_title("Radio 1 – Noise Floor")
    ax1_noise.grid(True, alpha=0.3)

    ax1_snr.plot(el1, snr1, color=C1, lw=1.5, ls="--")
    ax1_snr.set_ylabel("SNR = RSSI − Noise")
    ax1_snr.set_ylim(0, 200)
    ax1_snr.set_xlabel("Time (s from start)")
    ax1_snr.set_title("Radio 1 – SNR")
    ax1_snr.grid(True, alpha=0.3)
    ax1_snr.xaxis.set_major_formatter(FuncFormatter(fmt_time))

    ax2_rssi.plot(el2, rssi2, color=C2, lw=1.5)
    ax2_rssi.set_ylabel("RSSI (raw, 0–255)")
    ax2_rssi.set_ylim(100, 300)
    ax2_rssi.set_title("Radio 2 – Signal Strength")
    ax2_rssi.grid(True, alpha=0.3)

    ax2_noise.plot(el2, nse2, color=C2, lw=1.5)
    ax2_noise.set_ylabel("Noise Floor (raw)")
    ax2_noise.set_ylim(0, 150)
    ax2_noise.set_title("Radio 2 – Noise Floor")
    ax2_noise.grid(True, alpha=0.3)

    ax2_snr.plot(el2, snr2, color=C2, lw=1.5, ls="--")
    ax2_snr.set_ylabel("SNR = RSSI − Noise")
    ax2_snr.set_ylim(0, 200)
    ax2_snr.set_xlabel("Time (s from start)")
    ax2_snr.set_title("Radio 2 – SNR")
    ax2_snr.grid(True, alpha=0.3)
    ax2_snr.xaxis.set_major_formatter(FuncFormatter(fmt_time))

    # Initial x range
    ax_diff.set_xlim(x_min - x_margin, x_max + x_margin)

    # ── Crosshair cursor (follows mouse across all subplots) ──────────────
    multi_cursor = MultiCursor(
        fig.canvas, all_axes,
        color="red", lw=0.8,
        horizOn=False, vertOn=True,
        useblit=True,
    )

    # ── Measurement cursors A and B ───────────────────────────────────────
    cursor_lines: dict[str, list] = {"A": [], "B": []}
    cursor_x:     dict[str, float | None] = {"A": None, "B": None}

    # Status bar at the bottom of the figure
    status_text = fig.text(
        0.5, 0.005,
        "Left-click: cursor A  |  Right-click: cursor B  |  C / Esc: clear / reset",
        ha="center", va="bottom", fontsize=8, color="dimgrey",
        transform=fig.transFigure,
    )

    def _update_status() -> None:
        xa, xb = cursor_x["A"], cursor_x["B"]
        parts: list[str] = []
        if xa is not None:
            parts.append(f"A = {xa:.2f} s")
        if xb is not None:
            parts.append(f"B = {xb:.2f} s")
        if xa is not None and xb is not None:
            parts.append(f"ΔT = {abs(xb - xa):.3f} s")
        if parts:
            status_text.set_text("   |   ".join(parts))
        else:
            status_text.set_text(
                "Left-click: cursor A  |  Right-click: cursor B  |  C / Esc: clear / reset"
            )

    def _draw_cursor(name: str, x: float) -> None:
        for ln in cursor_lines[name]:
            ln.remove()
        cursor_lines[name] = []
        color = "#1560bd" if name == "A" else "#2ca02c"
        for ax in all_axes:
            ln = ax.axvline(x, color=color, lw=1.3, ls="--", alpha=0.85)
            cursor_lines[name].append(ln)
        cursor_x[name] = x
        _update_status()
        fig.canvas.draw_idle()

    def _clear_cursors() -> None:
        for name in ("A", "B"):
            for ln in cursor_lines[name]:
                ln.remove()
            cursor_lines[name] = []
            cursor_x[name] = None
        _update_status()
        fig.canvas.draw_idle()

    # ── Scroll-wheel zoom on time axis ────────────────────────────────────
    ZOOM_FACTOR = 0.82   # fraction of current span kept per scroll-up tick

    def on_scroll(event) -> None:
        if event.inaxes not in all_axes or event.xdata is None:
            return
        # Don't steal scrolls when a toolbar mode is active (pan / zoom-box)
        if fig.canvas.toolbar and fig.canvas.toolbar.mode:
            return
        xlim = ax_diff.get_xlim()
        span = xlim[1] - xlim[0]
        factor = ZOOM_FACTOR if event.button == "up" else (1.0 / ZOOM_FACTOR)
        new_span = span * factor
        ratio = (event.xdata - xlim[0]) / span
        ax_diff.set_xlim(event.xdata - ratio * new_span,
                         event.xdata + (1.0 - ratio) * new_span)
        fig.canvas.draw_idle()

    # ── Mouse click → place measurement cursor ────────────────────────────
    def on_click(event) -> None:
        if event.inaxes not in all_axes or event.xdata is None:
            return
        # Don't interfere with toolbar pan/zoom
        if fig.canvas.toolbar and fig.canvas.toolbar.mode:
            return
        if event.button == 1:
            _draw_cursor("A", event.xdata)
        elif event.button == 3:
            _draw_cursor("B", event.xdata)

    # ── Key bindings ──────────────────────────────────────────────────────
    def on_key(event) -> None:
        key = (event.key or "").lower()
        if key == "c":
            _clear_cursors()
        elif key == "escape":
            _clear_cursors()
            ax_diff.set_xlim(x_min - x_margin, x_max + x_margin)
            fig.canvas.draw_idle()

    fig.canvas.mpl_connect("scroll_event",       on_scroll)
    fig.canvas.mpl_connect("button_press_event", on_click)
    fig.canvas.mpl_connect("key_press_event",    on_key)

    plt.tight_layout(rect=[0, 0.025, 1, 0.97])
    plt.show()


if __name__ == "__main__":
    main()
