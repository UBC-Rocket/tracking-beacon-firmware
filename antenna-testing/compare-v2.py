"""
Antenna comparison: aligned request-response RSSI logger for two SiK radios.

Both radios send ATI7 simultaneously (barrier-synced), read concurrently,
and are paired by poll index so the diff is always same-moment measurements.

Usage:
    python rssi-test-time.py --port1 /dev/tty.usbserial-AAA
                             --port2 /dev/tty.usbserial-BBB
                             --baud 57600

Outputs:
    rssi_r1_<ts>.csv    – full data from radio 1
    rssi_r2_<ts>.csv    – full data from radio 2
    rssi_diff_<ts>.csv  – per-poll diff (r1 − r2), with both send_times

Dependencies:
    pip install pexpect matplotlib
"""

import argparse
import csv
import queue
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
    import matplotlib.gridspec as gridspec
    from matplotlib.ticker import FuncFormatter
except ImportError:
    print("Missing dependency: pip install matplotlib")
    sys.exit(1)


RSSI_LINE_PATTERN = re.compile(r"L/R RSSI Pkts: L/R/P\s+(.*)")
NODE_PATTERN      = re.compile(r"(\d+):(\d+)/(\d+)/(\d+)")
NOISE_PATTERN     = re.compile(r"L/R noise: (\d+)/(\d+)")
STATS_PATTERN     = re.compile(
    r"txe=(\d+)\s+rxe=(\d+)\s+stx=(\d+)\s+srx=(\d+)\s+ecc=(\d+)/(\d+)\s+temp=(\d+)\s+dco=(\d+)"
)

MAX_POINTS = 300

CSV_HEADER = [
    "poll_idx",
    "send_time",
    "rtt_ms",
    "node_id",
    "local_rssi", "remote_rssi", "packets",
    "local_noise", "remote_noise",
    "local_snr", "remote_snr",
    "txe", "rxe", "stx", "srx",
    "ecc_local", "ecc_remote",
    "temp", "dco",
]

DIFF_CSV_HEADER = [
    "poll_idx",
    "send_time_r1",
    "send_time_r2",
    "node_id",
    "local_rssi_diff",   # r1 − r2
    "local_snr_diff",
    "rtt_r1_ms",
    "rtt_r2_ms",
]

# Sentinel for a poll that timed out
TIMEOUT = object()


class RadioPlotState:
    def __init__(self):
        self.lock       = threading.Lock()
        self.timestamps = deque(maxlen=MAX_POINTS)
        self.rssi_local = deque(maxlen=MAX_POINTS)
        self.noise_local= deque(maxlen=MAX_POINTS)
        self.snr_local  = deque(maxlen=MAX_POINTS)


radio1_plot = RadioPlotState()
radio2_plot = RadioPlotState()

diff_lock       = threading.Lock()
diff_timestamps = deque(maxlen=MAX_POINTS)
diff_rssi_buf   = deque(maxlen=MAX_POINTS)
diff_snr_buf    = deque(maxlen=MAX_POINTS)

running    = True
child_procs= []
plot_t0    = None
plot_t0_lock = threading.Lock()


# ── Radio connection ──────────────────────────────────────────────────────────

def connect_radio(port: str, baud: int, label: str):
    cmd = f"picocom -b {baud} {port}"
    print(f"[{label}] Launching: {cmd}")
    child = pexpect.spawn(cmd, timeout=30, encoding="utf-8")
    child_procs.append(child)

    try:
        child.expect("Terminal ready", timeout=15)
    except pexpect.TIMEOUT:
        print(f"[{label}] ERROR: Timed out waiting for picocom. Check port/baud.")
        return None
    except pexpect.EOF:
        print(f"[{label}] ERROR: picocom exited unexpectedly.")
        return None

    print(f"[{label}] Connected. Entering AT command mode...")
    time.sleep(1.0)
    child.send("+++")
    time.sleep(1.2)
    child.timeout = 0.5
    print(f"[{label}] Ready.")
    return child


# ── Poller ────────────────────────────────────────────────────────────────────

def poller_loop(
    port: str,
    baud: int,
    label: str,
    radio_num: int,          # 1 or 2
    poll_barrier: threading.Barrier,
    pair_state: dict,
    pair_queue: queue.Queue,
) -> None:
    global running

    child = connect_radio(port, baud, label)
    if child is None:
        running = False
        # unblock the barrier so the other thread doesn't hang
        try:
            poll_barrier.abort()
        except Exception:
            pass
        return

    # Wait for both radios to finish connecting before polling starts
    try:
        poll_barrier.wait(timeout=30)
    except threading.BrokenBarrierError:
        running = False
        return

    print(f"[{label}] Starting polling...")

    send_dt   = datetime.now()
    send_perf = time.perf_counter()

    def send_ati7():
        nonlocal send_dt, send_perf
        send_dt   = datetime.now()
        send_perf = time.perf_counter()
        child.send("ATI7\r")

    while running:
        # ── Sync point: both radios send ATI7 simultaneously ─────────────
        try:
            poll_barrier.wait()
        except threading.BrokenBarrierError:
            break

        send_ati7()

        # ── Read 3-line response ──────────────────────────────────────────
        block: dict | None = {}
        rtt_ms = None

        while running and block is not None:
            try:
                line = child.readline()
                if not line:
                    continue
                line = line.strip()

                rssi_m  = RSSI_LINE_PATTERN.search(line)
                noise_m = NOISE_PATTERN.search(line)
                stats_m = STATS_PATTERN.search(line)

                if rssi_m:
                    rtt_ms = (time.perf_counter() - send_perf) * 1000
                    nodes  = {}
                    for nm in NODE_PATTERN.finditer(rssi_m.group(1)):
                        nid = int(nm.group(1))
                        l   = int(nm.group(2))
                        r   = int(nm.group(3))
                        p   = int(nm.group(4))
                        if p > 0:
                            nodes[nid] = {"local_rssi": l, "remote_rssi": r, "packets": p}
                    block = {"nodes": nodes}

                elif noise_m and block and block.get("nodes") is not None:
                    block["local_noise"]  = int(noise_m.group(1))
                    block["remote_noise"] = int(noise_m.group(2))

                elif stats_m and block and block.get("nodes") is not None and "local_noise" in block:
                    block["txe"]        = int(stats_m.group(1))
                    block["rxe"]        = int(stats_m.group(2))
                    block["stx"]        = int(stats_m.group(3))
                    block["srx"]        = int(stats_m.group(4))
                    block["ecc_local"]  = int(stats_m.group(5))
                    block["ecc_remote"] = int(stats_m.group(6))
                    block["temp"]       = int(stats_m.group(7))
                    block["dco"]        = int(stats_m.group(8))
                    block["send_time"]  = send_dt
                    block["rtt_ms"]     = rtt_ms
                    break  # complete block

            except pexpect.TIMEOUT:
                print(f"[{label}] Timeout — no response for this poll")
                block = TIMEOUT
                break
            except pexpect.EOF:
                print(f"\n[{label}] Connection closed.")
                running = False
                block = TIMEOUT
                break
            except Exception as e:
                if running:
                    print(f"\n[{label}] Read error: {e}")
                running = False
                block = TIMEOUT
                break

        # ── Pair with the other radio's result ───────────────────────────
        key = f"r{radio_num}"
        other_key = "r2" if radio_num == 1 else "r1"

        with pair_state["lock"]:
            pair_state[key] = block

            if pair_state[other_key] is not None:
                # Both radios have responded — enqueue the pair
                r1 = pair_state["r1"]
                r2 = pair_state["r2"]
                pair_queue.put({
                    "idx":   pair_state["poll_idx"],
                    "radio1": r1,
                    "radio2": r2,
                })
                pair_state["r1"] = None
                pair_state["r2"] = None
                pair_state["poll_idx"] += 1

    running = False
    try:
        poll_barrier.abort()
    except Exception:
        pass


# ── Processor ─────────────────────────────────────────────────────────────────

def processor_loop(
    pair_queue: queue.Queue,
    csv1_path: str,
    csv2_path: str,
    diff_csv_path: str,
) -> None:
    global plot_t0

    with (
        open(csv1_path,    "w", newline="") as f1,
        open(csv2_path,    "w", newline="") as f2,
        open(diff_csv_path,"w", newline="") as fd,
    ):
        w1   = csv.writer(f1)
        w2   = csv.writer(f2)
        wd   = csv.writer(fd)
        w1.writerow(CSV_HEADER)
        w2.writerow(CSV_HEADER)
        wd.writerow(DIFF_CSV_HEADER)

        while running or not pair_queue.empty():
            try:
                pair = pair_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            idx   = pair["idx"]
            b1    = pair["radio1"]
            b2    = pair["radio2"]

            def write_radio(writer, block, idx):
                if block is TIMEOUT or block is None:
                    return
                local_noise  = block["local_noise"]
                remote_noise = block["remote_noise"]
                for node_id, nd in block["nodes"].items():
                    l_rssi = nd["local_rssi"]
                    r_rssi = nd["remote_rssi"]
                    pkts   = nd["packets"]
                    l_snr  = l_rssi - local_noise
                    r_snr  = r_rssi - remote_noise
                    writer.writerow([
                        idx,
                        block["send_time"].isoformat(),
                        f"{block['rtt_ms']:.2f}",
                        node_id,
                        l_rssi, r_rssi, pkts,
                        local_noise, remote_noise,
                        l_snr, r_snr,
                        block["txe"], block["rxe"],
                        block["stx"], block["srx"],
                        block["ecc_local"], block["ecc_remote"],
                        block["temp"], block["dco"],
                    ])

            write_radio(w1, b1, idx)
            write_radio(w2, b2, idx)

            # ── Diff (only when both polls succeeded) ─────────────────────
            if b1 is not TIMEOUT and b2 is not TIMEOUT and b1 and b2:
                p1 = b1["nodes"].get(1) or next(iter(b1["nodes"].values()), None)
                p2 = b2["nodes"].get(1) or next(iter(b2["nodes"].values()), None)

                if p1 and p2:
                    l1_rssi = p1["local_rssi"]
                    l2_rssi = p2["local_rssi"]
                    l1_snr  = l1_rssi - b1["local_noise"]
                    l2_snr  = l2_rssi - b2["local_noise"]
                    rssi_diff = l1_rssi - l2_rssi
                    snr_diff  = l1_snr  - l2_snr

                    node_id = list(b1["nodes"].keys())[0]
                    wd.writerow([
                        idx,
                        b1["send_time"].isoformat(),
                        b2["send_time"].isoformat(),
                        node_id,
                        rssi_diff,
                        snr_diff,
                        f"{b1['rtt_ms']:.2f}",
                        f"{b2['rtt_ms']:.2f}",
                    ])

                    ts = b1["send_time"]
                    with plot_t0_lock:
                        if plot_t0 is None:
                            plot_t0 = ts

                    with radio1_plot.lock:
                        radio1_plot.timestamps.append(ts)
                        radio1_plot.rssi_local.append(l1_rssi)
                        radio1_plot.noise_local.append(b1["local_noise"])
                        radio1_plot.snr_local.append(l1_snr)

                    with radio2_plot.lock:
                        radio2_plot.timestamps.append(b2["send_time"])
                        radio2_plot.rssi_local.append(l2_rssi)
                        radio2_plot.noise_local.append(b2["local_noise"])
                        radio2_plot.snr_local.append(l2_snr)

                    with diff_lock:
                        diff_timestamps.append(ts)
                        diff_rssi_buf.append(rssi_diff)
                        diff_snr_buf.append(snr_diff)

                    print(
                        f"[{idx:>5}] "
                        f"R1 RSSI:{l1_rssi} SNR:{l1_snr} RTT:{b1['rtt_ms']:.1f}ms  |  "
                        f"R2 RSSI:{l2_rssi} SNR:{l2_snr} RTT:{b2['rtt_ms']:.1f}ms  |  "
                        f"Δ RSSI:{rssi_diff:+d} SNR:{snr_diff:+d}"
                    )

            f1.flush()
            f2.flush()
            fd.flush()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aligned RSSI comparison for two SiK radios"
    )
    parser.add_argument("--port1", default="/dev/tty.usbserial-0001",
                        help="Serial port for radio 1")
    parser.add_argument("--port2", default="/dev/tty.usbserial-0002",
                        help="Serial port for radio 2")
    parser.add_argument("--baud", type=int, default=57600,
                        help="Baud rate for both radios (default: 57600)")
    args = parser.parse_args()

    ts_str        = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv1_path     = f"rssi_r1_{ts_str}.csv"
    csv2_path     = f"rssi_r2_{ts_str}.csv"
    diff_csv_path = f"rssi_diff_{ts_str}.csv"
    plot_path     = f"rssi_compare_{ts_str}.png"

    print(f"CSV Radio 1 -> {csv1_path}")
    print(f"CSV Radio 2 -> {csv2_path}")
    print(f"CSV Diff    -> {diff_csv_path}")
    print(f"Plot        -> {plot_path}")
    print("Press Ctrl+C or close the plot window to stop.\n")

    poll_barrier = threading.Barrier(2)
    pair_state   = {"lock": threading.Lock(), "poll_idx": 0, "r1": None, "r2": None}
    pair_queue   = queue.Queue()

    threading.Thread(
        target=poller_loop,
        args=(args.port1, args.baud, "Radio1", 1, poll_barrier, pair_state, pair_queue),
        daemon=True,
    ).start()
    threading.Thread(
        target=poller_loop,
        args=(args.port2, args.baud, "Radio2", 2, poll_barrier, pair_state, pair_queue),
        daemon=True,
    ).start()
    threading.Thread(
        target=processor_loop,
        args=(pair_queue, csv1_path, csv2_path, diff_csv_path),
        daemon=True,
    ).start()

    # ── Plot layout (5 rows) ──────────────────────────────────────────────
    fig = plt.figure(figsize=(18, 13))
    fig.suptitle(
        f"Antenna Comparison  —  {args.port1}  vs  {args.port2}  @ {args.baud} baud",
        fontsize=12,
    )
    gs = gridspec.GridSpec(5, 2, figure=fig, hspace=0.35, wspace=0.28)

    ax_rdiff  = fig.add_subplot(gs[0, :])   # RSSI diff full-width
    ax_sdiff  = fig.add_subplot(gs[1, :])   # SNR diff full-width

    ax1_rssi  = fig.add_subplot(gs[2, 0])
    ax1_noise = fig.add_subplot(gs[3, 0], sharex=ax1_rssi)
    ax1_snr   = fig.add_subplot(gs[4, 0], sharex=ax1_rssi)

    ax2_rssi  = fig.add_subplot(gs[2, 1])
    ax2_noise = fig.add_subplot(gs[3, 1], sharex=ax2_rssi)
    ax2_snr   = fig.add_subplot(gs[4, 1], sharex=ax2_rssi)

    C1 = "#1f77b4"   # blue
    C2 = "#ff7f0e"   # orange

    fmt = FuncFormatter(lambda x, _: f"{int(x)}s")

    line_rdiff,  = ax_rdiff.plot([], [], color="purple", lw=1.8)
    ax_rdiff.axhline(0, color="grey", lw=0.8, ls="--")
    ax_rdiff.set_ylabel("RSSI diff (R1 − R2)")
    ax_rdiff.set_ylim(-128, 128)
    ax_rdiff.set_title("Local RSSI Difference  (R1 − R2)")
    ax_rdiff.grid(True, alpha=0.3)
    ax_rdiff.xaxis.set_major_formatter(fmt)

    line_sdiff,  = ax_sdiff.plot([], [], color="#2ca02c", lw=1.8)
    ax_sdiff.axhline(0, color="grey", lw=0.8, ls="--")
    ax_sdiff.set_ylabel("SNR diff (R1 − R2)")
    ax_sdiff.set_ylim(-128, 128)
    ax_sdiff.set_title("Local SNR Difference  (R1 − R2)")
    ax_sdiff.grid(True, alpha=0.3)
    ax_sdiff.xaxis.set_major_formatter(fmt)

    line1_rssi,  = ax1_rssi.plot([], [], color=C1, lw=1.5)
    ax1_rssi.set_ylabel("RSSI (raw)")
    ax1_rssi.set_ylim(100, 260)
    ax1_rssi.set_title("Radio 1 – Signal Strength")
    ax1_rssi.grid(True, alpha=0.3)

    line1_noise, = ax1_noise.plot([], [], color=C1, lw=1.5)
    ax1_noise.set_ylabel("Noise Floor")
    ax1_noise.set_ylim(0, 150)
    ax1_noise.set_title("Radio 1 – Noise Floor")
    ax1_noise.grid(True, alpha=0.3)

    line1_snr,   = ax1_snr.plot([], [], color=C1, lw=1.5, ls="--")
    ax1_snr.set_ylabel("SNR")
    ax1_snr.set_ylim(0, 200)
    ax1_snr.set_xlabel("Time (s)")
    ax1_snr.set_title("Radio 1 – SNR")
    ax1_snr.grid(True, alpha=0.3)
    ax1_snr.xaxis.set_major_formatter(fmt)

    line2_rssi,  = ax2_rssi.plot([], [], color=C2, lw=1.5)
    ax2_rssi.set_ylabel("RSSI (raw)")
    ax2_rssi.set_ylim(100, 260)
    ax2_rssi.set_title("Radio 2 – Signal Strength")
    ax2_rssi.grid(True, alpha=0.3)

    line2_noise, = ax2_noise.plot([], [], color=C2, lw=1.5)
    ax2_noise.set_ylabel("Noise Floor")
    ax2_noise.set_ylim(0, 150)
    ax2_noise.set_title("Radio 2 – Noise Floor")
    ax2_noise.grid(True, alpha=0.3)

    line2_snr,   = ax2_snr.plot([], [], color=C2, lw=1.5, ls="--")
    ax2_snr.set_ylabel("SNR")
    ax2_snr.set_ylim(0, 200)
    ax2_snr.set_xlabel("Time (s)")
    ax2_snr.set_title("Radio 2 – SNR")
    ax2_snr.grid(True, alpha=0.3)
    ax2_snr.xaxis.set_major_formatter(fmt)

    ALL_LINES = (
        line_rdiff, line_sdiff,
        line1_rssi, line1_noise, line1_snr,
        line2_rssi, line2_noise, line2_snr,
    )

    def to_elapsed(ts_list):
        with plot_t0_lock:
            t0 = plot_t0
        if t0 is None:
            return []
        return [(t - t0).total_seconds() for t in ts_list]

    def set_xlim(ax, el):
        lo, hi = el[0], el[-1]
        if hi - lo < 30:
            hi = lo + 30
        ax.set_xlim(lo, hi)

    def update(_frame):
        with radio1_plot.lock:
            ts1 = list(radio1_plot.timestamps)
            lr1 = list(radio1_plot.rssi_local)
            ln1 = list(radio1_plot.noise_local)
            ls1 = list(radio1_plot.snr_local)

        with radio2_plot.lock:
            ts2 = list(radio2_plot.timestamps)
            lr2 = list(radio2_plot.rssi_local)
            ln2 = list(radio2_plot.noise_local)
            ls2 = list(radio2_plot.snr_local)

        with diff_lock:
            dts  = list(diff_timestamps)
            drssi= list(diff_rssi_buf)
            dsnr = list(diff_snr_buf)

        el_d = to_elapsed(dts)
        if len(el_d) >= 2:
            set_xlim(ax_rdiff, el_d)
            set_xlim(ax_sdiff, el_d)
            line_rdiff.set_data(el_d, drssi)
            line_sdiff.set_data(el_d, dsnr)

        el1 = to_elapsed(ts1)
        if len(el1) >= 2:
            set_xlim(ax1_rssi, el1)
            line1_rssi.set_data(el1, lr1)
            line1_noise.set_data(el1, ln1)
            line1_snr.set_data(el1, ls1)

        el2 = to_elapsed(ts2)
        if len(el2) >= 2:
            set_xlim(ax2_rssi, el2)
            line2_rssi.set_data(el2, lr2)
            line2_noise.set_data(el2, ln2)
            line2_snr.set_data(el2, ls2)

        return ALL_LINES

    ani = animation.FuncAnimation(  # noqa: F841
        fig, update, interval=1000, blit=False, cache_frame_data=False
    )

    def save_plot():
        if diff_timestamps:
            fig.savefig(plot_path, dpi=150, bbox_inches="tight")
            print(f"\nPlot saved -> {plot_path}")

    def on_close(_event):
        global running
        running = False
        for p in child_procs:
            try:
                p.close(force=True)
            except Exception:
                pass
        save_plot()

    fig.canvas.mpl_connect("close_event", on_close)

    try:
        plt.tight_layout()
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
