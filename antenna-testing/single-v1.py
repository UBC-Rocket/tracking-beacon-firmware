#!/usr/bin/env python3
"""
RSSI logger and live plotter for SiK radio module via picocom.

Usage:
    python rssi-logging.py --port /dev/tty.usbserial-0001 --baud 57600

Dependencies:
    pip install pexpect matplotlib
"""

import argparse
import csv
import re
import sys
import time
import threading
from datetime import datetime, timedelta
from collections import deque

try:
    import pexpect
except ImportError:
    print("Missing dependency: pip install pexpect")
    sys.exit(1)

try:
    import matplotlib.pyplot as plt
    import matplotlib.animation as animation
    import matplotlib.dates as mdates
except ImportError:
    print("Missing dependency: pip install matplotlib")
    sys.exit(1)


# Matches the full RSSI line, capturing the node list portion after "L/R/P"
RSSI_LINE_PATTERN = re.compile(r"L/R RSSI Pkts: L/R/P\s+(.*)")
# Matches an individual node entry: node_id:local/remote/packets
NODE_PATTERN = re.compile(r"(\d+):(\d+)/(\d+)/(\d+)")
# Matches: L/R noise: 57/60
NOISE_PATTERN = re.compile(r"L/R noise: (\d+)/(\d+)")
# Matches the stats line: txe=0 rxe=109 stx=0 srx=0 ecc=0/0 temp=25 dco=0
STATS_PATTERN = re.compile(
    r"txe=(\d+)\s+rxe=(\d+)\s+stx=(\d+)\s+srx=(\d+)\s+ecc=(\d+)/(\d+)\s+temp=(\d+)\s+dco=(\d+)"
)

MAX_POINTS = 300

data_lock = threading.Lock()
timestamps       = deque(maxlen=MAX_POINTS)
rssi_local_buf   = deque(maxlen=MAX_POINTS)
rssi_remote_buf  = deque(maxlen=MAX_POINTS)
noise_local_buf  = deque(maxlen=MAX_POINTS)
noise_remote_buf = deque(maxlen=MAX_POINTS)
snr_local_buf    = deque(maxlen=MAX_POINTS)
snr_remote_buf   = deque(maxlen=MAX_POINTS)

running = True
child_proc = None

CSV_HEADER = [
    "timestamp",
    "node_id",
    "local_rssi", "remote_rssi", "packets",
    "local_noise", "remote_noise",
    "local_snr", "remote_snr",
    "txe", "rxe", "stx", "srx",
    "ecc_local", "ecc_remote",
    "temp", "dco",
]


def read_loop(port: str, baud: int, csv_path: str) -> None:
    global running, child_proc

    cmd = f"picocom -b {baud} {port}"
    print(f"Launching: {cmd}\n")

    child_proc = pexpect.spawn(cmd, timeout=30, encoding="utf-8")

    try:
        child_proc.expect("Terminal ready", timeout=15)
    except pexpect.TIMEOUT:
        print("ERROR: Timed out waiting for picocom to connect. Check port/baud.")
        running = False
        return
    except pexpect.EOF:
        print("ERROR: picocom exited unexpectedly.")
        running = False
        return

    print("Connected. Entering AT command mode (waiting 1s)...")
    time.sleep(1.0)
    child_proc.send("+++")
    time.sleep(1.2)  # Hayes escape: must be 1s silence before and after +++

    print("Sending AT&T=RSSI...")
    child_proc.send("AT&T=RSSI\r")

    # Accumulate a 3-line block: RSSI line → noise line → stats line
    block: dict = {}

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)

        while running:
            try:
                line = child_proc.readline()
                if not line:
                    continue
                line = line.strip()

                rssi_line_m = RSSI_LINE_PATTERN.search(line)
                noise_m     = NOISE_PATTERN.search(line)
                stats_m     = STATS_PATTERN.search(line)

                # ── Line 1: RSSI + packet counts for all nodes ───────────────
                if rssi_line_m:
                    nodes = {}
                    for nm in NODE_PATTERN.finditer(rssi_line_m.group(1)):
                        node_id  = int(nm.group(1))
                        l_rssi   = int(nm.group(2))
                        r_rssi   = int(nm.group(3))
                        pkts     = int(nm.group(4))
                        if pkts > 0:  # skip inactive nodes
                            nodes[node_id] = {
                                "local_rssi":  l_rssi,
                                "remote_rssi": r_rssi,
                                "packets":     pkts,
                            }
                    block = {"nodes": nodes, "time": datetime.now()}

                # ── Line 2: Noise floor ──────────────────────────────────────
                elif noise_m and block.get("nodes") is not None:
                    block["local_noise"]  = int(noise_m.group(1))
                    block["remote_noise"] = int(noise_m.group(2))

                # ── Line 3: Stats — flush the complete block ─────────────────
                elif stats_m and block.get("nodes") is not None and "local_noise" in block:
                    ts          = block["time"]
                    local_noise = block["local_noise"]
                    remote_noise= block["remote_noise"]
                    txe         = int(stats_m.group(1))
                    rxe         = int(stats_m.group(2))
                    stx         = int(stats_m.group(3))
                    srx         = int(stats_m.group(4))
                    ecc_local   = int(stats_m.group(5))
                    ecc_remote  = int(stats_m.group(6))
                    temp        = int(stats_m.group(7))
                    dco         = int(stats_m.group(8))

                    # Write one CSV row per active node
                    for node_id, nd in block["nodes"].items():
                        l_rssi  = nd["local_rssi"]
                        r_rssi  = nd["remote_rssi"]
                        pkts    = nd["packets"]
                        l_snr   = l_rssi - local_noise
                        r_snr   = r_rssi - remote_noise
                        writer.writerow([
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
                        l_snr = primary["local_rssi"]  - local_noise
                        r_snr = primary["remote_rssi"] - remote_noise
                        with data_lock:
                            timestamps.append(ts)
                            rssi_local_buf.append(primary["local_rssi"])
                            rssi_remote_buf.append(primary["remote_rssi"])
                            noise_local_buf.append(local_noise)
                            noise_remote_buf.append(remote_noise)
                            snr_local_buf.append(l_snr)
                            snr_remote_buf.append(r_snr)

                        active_nodes = list(block["nodes"].keys())
                        print(
                            f"[{ts.strftime('%H:%M:%S')}] "
                            f"RSSI loc:{primary['local_rssi']} rem:{primary['remote_rssi']}  "
                            f"Noise loc:{local_noise} rem:{remote_noise}  "
                            f"SNR loc:{l_snr} rem:{r_snr}  "
                            f"Pkts:{primary['packets']}  "
                            f"rxe:{rxe} temp:{temp}°C  "
                            f"active nodes:{active_nodes}"
                        )

                    block = {}

            except pexpect.EOF:
                print("\nConnection closed by picocom.")
                break
            except Exception as e:
                if running:
                    print(f"\nRead error: {e}")
                break

    running = False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Log and plot RSSI from SiK radio via picocom"
    )
    parser.add_argument(
        "--port", default="/dev/tty.usbserial-0001",
        help="Serial port (default: /dev/tty.usbserial-0001)"
    )
    parser.add_argument(
        "--baud", type=int, default=57600,
        help="Baud rate (default: 57600)"
    )
    parser.add_argument(
        "--output", default=None,
        help="CSV output filename (default: rssi_YYYYMMDD_HHMMSS.csv)"
    )
    args = parser.parse_args()

    ts_str    = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path  = args.output or f"rssi_{ts_str}.csv"
    plot_path = f"rssi_{ts_str}.png"

    print(f"CSV  -> {csv_path}")
    print(f"Plot -> {plot_path}  (also saved on exit)")
    print("Press Ctrl+C or close the plot window to stop.\n")

    thread = threading.Thread(
        target=read_loop, args=(args.port, args.baud, csv_path), daemon=True
    )
    thread.start()

    # ── Live plot setup (3 subplots, shared x-axis) ──────────────────────────
    fig, (ax_rssi, ax_noise, ax_snr) = plt.subplots(
        3, 1, figsize=(13, 9), sharex=True
    )
    fig.suptitle(f"SiK Radio Link Quality  —  {args.port} @ {args.baud}", fontsize=13)

    LOCAL_COLOR  = "#1f77b4"  # blue
    REMOTE_COLOR = "#d62728"  # red

    # RSSI
    line_loc_rssi, = ax_rssi.plot([], [], color=LOCAL_COLOR,  lw=1.5, label="Local RSSI")
    # line_rem_rssi, = ax_rssi.plot([], [], color=REMOTE_COLOR, lw=1.5, label="Remote RSSI")
    ax_rssi.set_ylabel("RSSI (raw, 0–255)")
    ax_rssi.set_ylim(0, 255)
    ax_rssi.legend(loc="upper left", fontsize=9)
    ax_rssi.grid(True, alpha=0.3)
    ax_rssi.set_title("Signal Strength  (higher = stronger)")

    # Noise
    line_loc_noise, = ax_noise.plot([], [], color=LOCAL_COLOR,  lw=1.5, label="Local Noise")
    line_rem_noise, = ax_noise.plot([], [], color=REMOTE_COLOR, lw=1.5, label="Remote Noise")
    ax_noise.set_ylabel("Noise Floor (raw)")
    ax_noise.set_ylim(0, 150)
    ax_noise.legend(loc="upper left", fontsize=9)
    ax_noise.grid(True, alpha=0.3)
    ax_noise.set_title("Noise Floor  (lower = better)")

    # SNR
    line_loc_snr, = ax_snr.plot([], [], color=LOCAL_COLOR,  lw=1.5, ls="--", label="Local SNR")
    line_rem_snr, = ax_snr.plot([], [], color=REMOTE_COLOR, lw=1.5, ls="--", label="Remote SNR")
    ax_snr.set_ylabel("SNR = RSSI − Noise (raw)")
    ax_snr.set_ylim(0, 200)
    ax_snr.set_xlabel("Time")
    ax_snr.legend(loc="upper left", fontsize=9)
    ax_snr.grid(True, alpha=0.3)
    ax_snr.set_title("Signal-to-Noise Ratio  (higher = better link margin)")

    ax_snr.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))

    ALL_LINES = (
        line_loc_rssi,
        line_loc_noise, line_rem_noise,
        line_loc_snr, line_rem_snr,
    )

    def save_plot() -> None:
        if timestamps:
            fig.savefig(plot_path, dpi=150, bbox_inches="tight")
            print(f"\nPlot saved -> {plot_path}")

    def on_close(_event) -> None:
        global running
        running = False
        if child_proc:
            try:
                child_proc.close(force=True)
            except Exception:
                pass
        save_plot()

    fig.canvas.mpl_connect("close_event", on_close)

    def update(_frame):
        with data_lock:
            if len(timestamps) < 2:
                return ALL_LINES

            ts  = list(timestamps)
            lr  = list(rssi_local_buf)
            # rr  = list(rssi_remote_buf)
            ln  = list(noise_local_buf)
            rn  = list(noise_remote_buf)
            ls  = list(snr_local_buf)
            rs  = list(snr_remote_buf)

        x_min = ts[0]
        x_max = ts[-1]
        if (x_max - x_min) < timedelta(seconds=30):
            x_max = x_min + timedelta(seconds=30)

        ax_rssi.set_xlim(x_min, x_max)

        line_loc_rssi.set_data(ts, lr)
        # line_rem_rssi.set_data(ts, rr)
        line_loc_noise.set_data(ts, ln)
        line_rem_noise.set_data(ts, rn)
        line_loc_snr.set_data(ts, ls)
        line_rem_snr.set_data(ts, rs)

        fig.autofmt_xdate()
        return ALL_LINES

    ani = animation.FuncAnimation(  # noqa: F841  (kept alive by reference)
        fig, update, interval=1000, blit=False, cache_frame_data=False
    )

    try:
        plt.tight_layout()
        plt.show()
    except KeyboardInterrupt:
        pass
    finally:
        global running
        running = False
        if child_proc:
            try:
                child_proc.close(force=True)
            except Exception:
                pass
        save_plot()


if __name__ == "__main__":
    main()
