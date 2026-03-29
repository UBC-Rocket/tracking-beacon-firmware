#!/usr/bin/env python3
"""
Antenna comparison: RSSI logger and live plotter for two SiK radio modules via picocom.

Usage:
    python rssi-compare.py --port1 /dev/tty.usbserial-0001
                           --port2 /dev/tty.usbserial-0002
                           --baud 57600

Outputs three CSVs:
    rssi_radio1_<ts>.csv  – full data from radio 1
    rssi_radio2_<ts>.csv  – full data from radio 2
    rssi_diff_<ts>.csv    – per-sample RSSI difference (radio1 − radio2)

Dependencies:
    pip install pexpect matplotlib
"""

import argparse
import csv
import re
import sys
import time
import threading
from datetime import datetime
from collections import deque

try:
    import pexpect
except ImportError:
    print("Missing dependency: pip install pexpect")
    sys.exit(1)

try:
    import matplotlib.pyplot as plt
    import matplotlib.animation as animation
    import matplotlib.gridspec as gridspec
    from matplotlib.ticker import FuncFormatter
except ImportError:
    print("Missing dependency: pip install matplotlib")
    sys.exit(1)


# Captures [seq_id] at the front, then the node list after "L/R/P"
RSSI_LINE_PATTERN = re.compile(r"\[(\d+)\].*L/R RSSI Pkts: L/R/P\s+(.*)")
# Individual node entry: node_id:local/remote/packets
NODE_PATTERN = re.compile(r"(\d+):(\d+)/(\d+)/(\d+)")
# L/R noise: 57/60
NOISE_PATTERN = re.compile(r"L/R noise: (\d+)/(\d+)")
# Stats line
STATS_PATTERN = re.compile(
    r"txe=(\d+)\s+rxe=(\d+)\s+stx=(\d+)\s+srx=(\d+)\s+ecc=(\d+)/(\d+)\s+temp=(\d+)\s+dco=(\d+)"
)

MAX_POINTS = 300

CSV_HEADER = [
    "seq_id",
    "timestamp",
    "node_id",
    "local_rssi", "remote_rssi", "packets",
    "local_noise", "remote_noise",
    "local_snr", "remote_snr",
    "txe", "rxe", "stx", "srx",
    "ecc_local", "ecc_remote",
    "temp", "dco",
]

DIFF_CSV_HEADER = [
    "timestamp",
    "node_id",
    "local_rssi_diff",  # radio1 local_rssi − radio2 local_rssi
    "local_snr_diff",   # radio1 local_snr  − radio2 local_snr
]


class RadioData:
    """All shared state for one radio, protected by a single lock."""
    def __init__(self):
        self.lock = threading.Lock()
        self.timestamps  = deque(maxlen=MAX_POINTS)
        self.rssi_local  = deque(maxlen=MAX_POINTS)
        self.noise_local = deque(maxlen=MAX_POINTS)
        self.snr_local   = deque(maxlen=MAX_POINTS)
        # Latest values for cross-radio diff computation
        self.latest_local_rssi = None
        self.latest_local_snr  = None
        # Node ID from [N] in the data stream, set on first receipt
        self.bracket_id: int | None = None


radio1 = RadioData()
radio2 = RadioData()

diff_lock       = threading.Lock()
diff_timestamps = deque(maxlen=MAX_POINTS)
diff_local_buf  = deque(maxlen=MAX_POINTS)  # local rssi diff
diff_snr_buf    = deque(maxlen=MAX_POINTS)  # local snr diff

running         = True
child_procs     = []
plot_start_time = None          # set on first data point from either radio
plot_time_lock  = threading.Lock()


def read_loop(
    port: str,
    baud: int,
    csv_path: str,
    radio: RadioData,
    label: str,
    other_radio: RadioData,
    diff_csv_path: str | None,
) -> None:
    global running

    cmd = f"picocom -b {baud} {port}"
    print(f"[{label}] Launching: {cmd}")

    child = pexpect.spawn(cmd, timeout=30, encoding="utf-8")
    child_procs.append(child)

    try:
        child.expect("Terminal ready", timeout=15)
    except pexpect.TIMEOUT:
        print(f"[{label}] ERROR: Timed out waiting for picocom. Check port/baud.")
        running = False
        return
    except pexpect.EOF:
        print(f"[{label}] ERROR: picocom exited unexpectedly.")
        running = False
        return

    print(f"[{label}] Connected. Entering AT command mode (waiting 1 s)...")
    time.sleep(1.0)
    child.send("+++")
    time.sleep(1.2)

    print(f"[{label}] Sending AT&T=RSSI...")
    child.send("AT&T=RSSI\r")

    block: dict = {}

    diff_file   = None
    diff_writer = None
    if diff_csv_path is not None:
        diff_file   = open(diff_csv_path, "w", newline="")
        diff_writer = csv.writer(diff_file)
        diff_writer.writerow(DIFF_CSV_HEADER)

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)

        while running:
            try:
                line = child.readline()
                if not line:
                    continue
                line = line.strip()

                rssi_line_m = RSSI_LINE_PATTERN.search(line)
                noise_m     = NOISE_PATTERN.search(line)
                stats_m     = STATS_PATTERN.search(line)

                # ── Line 1: sequence ID + RSSI / packet counts ───────────────
                if rssi_line_m:
                    seq_id = int(rssi_line_m.group(1))
                    nodes: dict = {}
                    for nm in NODE_PATTERN.finditer(rssi_line_m.group(2)):
                        node_id = int(nm.group(1))
                        l_rssi  = int(nm.group(2))
                        r_rssi  = int(nm.group(3))
                        pkts    = int(nm.group(4))
                        if pkts > 0:
                            nodes[node_id] = {
                                "local_rssi":  l_rssi,
                                "remote_rssi": r_rssi,
                                "packets":     pkts,
                            }
                    block = {"seq_id": seq_id, "nodes": nodes, "time": datetime.now()}

                # ── Line 2: noise floor ──────────────────────────────────────
                elif noise_m and block.get("nodes") is not None:
                    block["local_noise"]  = int(noise_m.group(1))
                    block["remote_noise"] = int(noise_m.group(2))

                # ── Line 3: stats — flush the complete block ─────────────────
                elif stats_m and block.get("nodes") is not None and "local_noise" in block:
                    ts           = block["time"]
                    seq_id       = block["seq_id"]
                    local_noise  = block["local_noise"]
                    remote_noise = block["remote_noise"]
                    txe          = int(stats_m.group(1))
                    rxe          = int(stats_m.group(2))
                    stx          = int(stats_m.group(3))
                    srx          = int(stats_m.group(4))
                    ecc_local    = int(stats_m.group(5))
                    ecc_remote   = int(stats_m.group(6))
                    temp         = int(stats_m.group(7))
                    dco          = int(stats_m.group(8))

                    for node_id, nd in block["nodes"].items():
                        l_rssi = nd["local_rssi"]
                        r_rssi = nd["remote_rssi"]
                        pkts   = nd["packets"]
                        l_snr  = l_rssi - local_noise
                        r_snr  = r_rssi - remote_noise
                        writer.writerow([
                            seq_id,
                            ts.isoformat(),
                            node_id,
                            l_rssi, r_rssi, pkts,
                            local_noise, remote_noise,
                            l_snr, r_snr,
                            txe, rxe, stx, srx,
                            ecc_local, ecc_remote,
                            temp, dco,
                        ])

                    f.flush()

                    # Update live plot buffers using node 1 (primary link)
                    primary = block["nodes"].get(1) or next(iter(block["nodes"].values()), None)
                    if primary:
                        l_rssi = primary["local_rssi"]
                        l_snr  = l_rssi - local_noise

                        global plot_start_time
                        with plot_time_lock:
                            if plot_start_time is None:
                                plot_start_time = ts

                        with radio.lock:
                            if radio.bracket_id is None:
                                radio.bracket_id = seq_id
                            radio.timestamps.append(ts)
                            radio.rssi_local.append(l_rssi)
                            radio.noise_local.append(local_noise)
                            radio.snr_local.append(l_snr)
                            radio.latest_local_rssi = l_rssi
                            radio.latest_local_snr  = l_snr

                        # Write diff CSV and update diff plot buffers
                        with other_radio.lock:
                            other_l   = other_radio.latest_local_rssi
                            other_snr = other_radio.latest_local_snr

                        if other_l is not None:
                            l_diff   = l_rssi - other_l
                            snr_diff = l_snr - (other_snr or 0)
                            if diff_writer is not None:
                                primary_node_id = list(block["nodes"].keys())[0]
                                diff_writer.writerow([ts.isoformat(), primary_node_id, l_diff, snr_diff])
                                diff_file.flush()
                            with diff_lock:
                                diff_timestamps.append(ts)
                                diff_local_buf.append(l_diff)
                                diff_snr_buf.append(snr_diff)

                        active_nodes = list(block["nodes"].keys())
                        print(
                            f"[{label} {ts.strftime('%H:%M:%S')}] "
                            f"RSSI loc:{l_rssi}  "
                            f"Noise loc:{local_noise}  "
                            f"SNR loc:{l_snr}  "
                            f"Pkts:{primary['packets']}  "
                            f"rxe:{rxe} temp:{temp}°C  "
                            f"nodes:{active_nodes}"
                        )

                    block = {}

            except pexpect.EOF:
                print(f"\n[{label}] Connection closed.")
                break
            except Exception as e:
                if running:
                    print(f"\n[{label}] Read error: {e}")
                break

    if diff_file is not None:
        diff_file.close()
    running = False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare RSSI from two SiK radios via picocom"
    )
    parser.add_argument("--port1", default="/dev/tty.usbserial-0001",
                        help="Serial port for radio 1 (default: /dev/tty.usbserial-0001)")
    parser.add_argument("--port2", default="/dev/tty.usbserial-0002",
                        help="Serial port for radio 2 (default: /dev/tty.usbserial-0002)")
    parser.add_argument("--baud", type=int, default=57600,
                        help="Baud rate for both radios (default: 57600)")
    args = parser.parse_args()

    ts_str       = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv1_path    = f"rssi_radio1_{ts_str}.csv"
    csv2_path    = f"rssi_radio2_{ts_str}.csv"
    diff_csv_path= f"rssi_diff_{ts_str}.csv"
    plot_path    = f"rssi_compare_{ts_str}.png"

    print(f"CSV Radio 1 -> {csv1_path}")
    print(f"CSV Radio 2 -> {csv2_path}")
    print(f"CSV Diff    -> {diff_csv_path}")
    print(f"Plot        -> {plot_path}  (saved on exit)")
    print("Press Ctrl+C or close the plot window to stop.\n")

    # radio1 writes the diff CSV (it computes diff against radio2's latest values)
    t1 = threading.Thread(
        target=read_loop,
        args=(args.port1, args.baud, csv1_path, radio1, "Radio1", radio2, diff_csv_path),
        daemon=True,
    )
    t2 = threading.Thread(
        target=read_loop,
        args=(args.port2, args.baud, csv2_path, radio2, "Radio2", radio1, None),
        daemon=True,
    )
    t1.start()
    t2.start()

    # ── Layout: 5 rows × 2 cols; rows 0-1 span both columns ──────────────
    fig = plt.figure(figsize=(18, 13))
    fig.suptitle(
        f"Antenna Comparison  —  "
        f"Radio 1: {args.port1}  |  Radio 2: {args.port2}  |  {args.baud} baud",
        fontsize=12,
    )
    gs = gridspec.GridSpec(5, 2, figure=fig, hspace=0.30, wspace=0.28)

    ax_diff     = fig.add_subplot(gs[0, :])         # full-width: RSSI diff
    ax_snr_diff = fig.add_subplot(gs[1, :])         # full-width: SNR diff

    ax1_rssi  = fig.add_subplot(gs[2, 0])           # left column
    ax1_noise = fig.add_subplot(gs[3, 0], sharex=ax1_rssi)
    ax1_snr   = fig.add_subplot(gs[4, 0], sharex=ax1_rssi)

    ax2_rssi  = fig.add_subplot(gs[2, 1])           # right column
    ax2_noise = fig.add_subplot(gs[3, 1], sharex=ax2_rssi)
    ax2_snr   = fig.add_subplot(gs[4, 1], sharex=ax2_rssi)

    C1 = "#1f77b4"   # blue   – radio 1
    C2 = "#ff7f0e"   # orange – radio 2

    # ── Top: local RSSI difference ────────────────────────────────────────
    line_diff, = ax_diff.plot([], [], color="purple", lw=1.8)
    ax_diff.axhline(0, color="grey", lw=0.8, ls="--")
    ax_diff.set_ylabel("RSSI diff")
    ax_diff.set_ylim(-128, 128)
    ax_diff.set_title("Local RSSI Difference  ([…] − […])")
    ax_diff.grid(True, alpha=0.3)
    ax_diff.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{int(x)}s"))

    # ── Second row: local SNR difference ─────────────────────────────────
    line_snr_diff, = ax_snr_diff.plot([], [], color="#2ca02c", lw=1.8)
    ax_snr_diff.axhline(0, color="grey", lw=0.8, ls="--")
    ax_snr_diff.set_ylabel("SNR diff")
    ax_snr_diff.set_ylim(-128, 128)
    ax_snr_diff.set_title("Local SNR Difference  ([…] − […])")
    ax_snr_diff.grid(True, alpha=0.3)
    ax_snr_diff.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{int(x)}s"))

    # ── Left: Radio 1 ─────────────────────────────────────────────────────
    line1_rssi, = ax1_rssi.plot([], [], color=C1, lw=1.5)
    ax1_rssi.set_ylabel("RSSI (raw, 0–255)")
    ax1_rssi.set_ylim(100, 300)
    ax1_rssi.set_title("[…] – Signal Strength")
    ax1_rssi.grid(True, alpha=0.3)

    line1_noise, = ax1_noise.plot([], [], color=C1, lw=1.5)
    ax1_noise.set_ylabel("Noise Floor (raw)")
    ax1_noise.set_ylim(0, 150)
    ax1_noise.set_title("[…] – Noise Floor")
    ax1_noise.grid(True, alpha=0.3)

    line1_snr, = ax1_snr.plot([], [], color=C1, lw=1.5, ls="--")
    ax1_snr.set_ylabel("SNR = RSSI − Noise")
    ax1_snr.set_ylim(0, 200)
    ax1_snr.set_xlabel("Time")
    ax1_snr.set_title("[…] – SNR")
    ax1_snr.grid(True, alpha=0.3)
    ax1_snr.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{int(x)}s"))

    # ── Right: Radio 2 ────────────────────────────────────────────────────
    line2_rssi, = ax2_rssi.plot([], [], color=C2, lw=1.5)
    ax2_rssi.set_ylabel("RSSI (raw, 0–255)")
    ax2_rssi.set_ylim(100, 300)
    ax2_rssi.set_title("[…] – Signal Strength")
    ax2_rssi.grid(True, alpha=0.3)

    line2_noise, = ax2_noise.plot([], [], color=C2, lw=1.5)
    ax2_noise.set_ylabel("Noise Floor (raw)")
    ax2_noise.set_ylim(0, 150)
    ax2_noise.set_title("[…] – Noise Floor")
    ax2_noise.grid(True, alpha=0.3)

    line2_snr, = ax2_snr.plot([], [], color=C2, lw=1.5, ls="--")
    ax2_snr.set_ylabel("SNR = RSSI − Noise")
    ax2_snr.set_ylim(0, 200)
    ax2_snr.set_xlabel("Time")
    ax2_snr.set_title("[…] – SNR")
    ax2_snr.grid(True, alpha=0.3)
    ax2_snr.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{int(x)}s"))

    ALL_LINES = (
        line_diff, line_snr_diff,
        line1_rssi, line1_noise, line1_snr,
        line2_rssi, line2_noise, line2_snr,
    )

    def save_plot() -> None:
        if radio1.timestamps or radio2.timestamps:
            fig.savefig(plot_path, dpi=150, bbox_inches="tight")
            print(f"\nPlot saved -> {plot_path}")

    def on_close(_event) -> None:
        global running
        running = False
        for p in child_procs:
            try:
                p.close(force=True)
            except Exception:
                pass
        save_plot()

    fig.canvas.mpl_connect("close_event", on_close)

    def _to_elapsed(ts_list: list) -> list:
        with plot_time_lock:
            t0 = plot_start_time
        if t0 is None:
            return []
        return [(t - t0).total_seconds() for t in ts_list]

    def _set_xlim(ax, elapsed: list) -> None:
        x_min = elapsed[0]
        x_max = elapsed[-1]
        if x_max - x_min < 30:
            x_max = x_min + 30
        ax.set_xlim(x_min, x_max)

    title_updated = [False]  # mutable flag so the nested function can set it

    def update(_frame):
        with radio1.lock:
            ts1  = list(radio1.timestamps)
            lr1  = list(radio1.rssi_local)
            ln1  = list(radio1.noise_local)
            ls1  = list(radio1.snr_local)
            bid1 = radio1.bracket_id

        with radio2.lock:
            ts2  = list(radio2.timestamps)
            lr2  = list(radio2.rssi_local)
            ln2  = list(radio2.noise_local)
            ls2  = list(radio2.snr_local)
            bid2 = radio2.bracket_id

        with diff_lock:
            dts  = list(diff_timestamps)
            dv   = list(diff_local_buf)
            dsnr = list(diff_snr_buf)

        # Update titles once both bracket IDs are known
        if not title_updated[0] and bid1 is not None and bid2 is not None:
            n1, n2 = f"[{bid1}]", f"[{bid2}]"
            ax_diff.set_title(f"Local RSSI Difference  ({n1} − {n2})")
            ax_snr_diff.set_title(f"Local SNR Difference  ({n1} − {n2})")
            ax1_rssi.set_title(f"{n1} – Signal Strength")
            ax1_noise.set_title(f"{n1} – Noise Floor")
            ax1_snr.set_title(f"{n1} – SNR")
            ax2_rssi.set_title(f"{n2} – Signal Strength")
            ax2_noise.set_title(f"{n2} – Noise Floor")
            ax2_snr.set_title(f"{n2} – SNR")
            title_updated[0] = True

        el_d = _to_elapsed(dts)
        if len(el_d) >= 2:
            _set_xlim(ax_diff, el_d)
            _set_xlim(ax_snr_diff, el_d)
            line_diff.set_data(el_d, dv)
            line_snr_diff.set_data(el_d, dsnr)

        el1 = _to_elapsed(ts1)
        if len(el1) >= 2:
            _set_xlim(ax1_rssi, el1)
            line1_rssi.set_data(el1, lr1)
            line1_noise.set_data(el1, ln1)
            line1_snr.set_data(el1, ls1)

        el2 = _to_elapsed(ts2)
        if len(el2) >= 2:
            _set_xlim(ax2_rssi, el2)
            line2_rssi.set_data(el2, lr2)
            line2_noise.set_data(el2, ln2)
            line2_snr.set_data(el2, ls2)

        return ALL_LINES

    ani = animation.FuncAnimation(  # noqa: F841
        fig, update, interval=1000, blit=False, cache_frame_data=False
    )

    try:
        plt.show()
    except KeyboardInterrupt:
        pass
    finally:
        global running
        running = False
        for p in child_procs:
            try:
                p.close(force=True)
            except Exception:
                pass
        save_plot()


if __name__ == "__main__":
    main()
