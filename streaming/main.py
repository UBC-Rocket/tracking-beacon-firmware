#!/usr/bin/env python3
"""
Simple UDP video stream receiver and display
Receives H264/H265 stream from cosmostreamer and displays it in a window
Records video continuously to disk as frames arrive
"""

import argparse
import numpy as np
import subprocess
import sys
import threading
import time
import queue
import signal
from datetime import datetime
import pyfakewebcam

from overlays import (
    # TODO: add static overlay back in after test 
    # launchGaugeOverlay, OverlayManager, StaticImageOverlay, StatusOverlay,
    GaugeOverlay, OverlayManager, StatusOverlay,
    TelemetryOverlay, TelemetrySource,
)

# Global shutdown flag for clean exit from any context
shutdown_flag = threading.Event()

# Configuration
UDP_PORT = 3000
FRAME_WIDTH = 1920  # Adjust to match your camera resolution
FRAME_HEIGHT = 1080
FRAME_SIZE = FRAME_WIDTH * FRAME_HEIGHT * 3  # 3 bytes per pixel (BGR)
VIDEO_FPS = 30  # Adjust based on your stream's FPS
DEFAULT_RADIO_SERIAL_PORT = "/tmp/telem_rx"
ENCODING = "H264"  # Change to "H265" if using H265 stream

# ---------------------------------------------------------------------------
# Shared state between threads
#
# _latest_raw_frame:    Most recent decoded frame from GStreamer (no overlays).
#                       Persists across stream drops so the overlay thread
#                       always has something to composite onto.
#
# _latest_output_frame: Most recent fully-composited frame (raw + overlays).
#                       Persists across overlay errors so the camera output
#                       loop always has something to send.
#
# _new_raw_frame_event: Signals the overlay thread that a fresh raw frame is
#                       available. The overlay thread does not block waiting
#                       for it — it has a timeout so it stays responsive to
#                       shutdown_flag and can re-render stale frames with
#                       updated telemetry even if the video stream is frozen.
# ---------------------------------------------------------------------------
_latest_raw_frame = None
_raw_frame_lock = threading.Lock()
_new_raw_frame_event = threading.Event()

_latest_output_frame = None
_output_frame_lock = threading.Lock()


def read_frames(process):
    """
    Thread 1 — GStreamer reader.

    Reads raw BGR frames from the GStreamer subprocess and stores the most
    recent one in _latest_raw_frame.  If the stream drops, the variable
    simply retains the last good frame so the overlay thread can continue
    compositing telemetry onto it.
    """
    global _latest_raw_frame

    while not shutdown_flag.is_set():
        try:
            raw_frame = process.stdout.read(FRAME_SIZE)
            if len(raw_frame) != FRAME_SIZE:
                print("Stream ended or incomplete frame")
                shutdown_flag.set()
                break

            # Convert raw bytes to numpy array (copy to make it writable)
            frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape(
                (FRAME_HEIGHT, FRAME_WIDTH, 3)
            ).copy()

            with _raw_frame_lock:
                _latest_raw_frame = frame

            # Signal overlay thread that a new frame is ready.
            # Using set() rather than put() means we never queue stale frames —
            # if the overlay thread is busy, it will pick up the newest frame
            # on its next iteration rather than processing every intermediate one.
            _new_raw_frame_event.set()

        except Exception as e:
            print(f"Error reading frame: {e}")
            break


def render_overlays(overlay_manager):
    """
    Thread 2 — Overlay renderer.

    Waits for new raw frames (with a timeout so it stays alive during stream
    drops), composites all overlays, and stores the result in
    _latest_output_frame.

    Isolation guarantees:
      - If the video stream drops, this thread keeps running and re-renders
        telemetry onto the last known raw frame.
      - If overlay rendering throws, the exception is caught and the raw
        frame is stored as-is, keeping _latest_output_frame alive.
      - TelemetrySource.get() is non-blocking and always returns the last
        known packet, so a serial dropout does not stall this thread.
    """
    global _latest_output_frame
    local_frame_count = 0

    while not shutdown_flag.is_set():
        # Wait up to 100 ms for a new raw frame.  The timeout means this
        # thread wakes periodically even when the stream is frozen, allowing
        # it to push updated telemetry text onto the last known video frame.
        _new_raw_frame_event.wait(timeout=0.1)
        _new_raw_frame_event.clear()

        with _raw_frame_lock:
            raw = _latest_raw_frame

        if raw is None:
            # No frame has arrived yet — nothing to composite onto.
            continue

        # Work on a copy so _latest_raw_frame is never mutated by overlays.
        frame = raw.copy()
        local_frame_count += 1

        try:
            context = {"frame_count": local_frame_count, "recording": True}
            frame = overlay_manager.render(frame, context)
        except Exception as e:
            # Overlay crash must not kill the stream.  Log the error and fall
            # through with the undecorated raw frame so the camera loop still
            # receives a valid image.
            print(f"[WARNING] Overlay render error (using raw frame): {e}")

        with _output_frame_lock:
            _latest_output_frame = frame


def main():
    global shutdown_flag

    parser = argparse.ArgumentParser(description="video stream receiver and display port")
    parser.add_argument(
        "--radio-port",
        default=DEFAULT_RADIO_SERIAL_PORT,
        help=f"Serial port for radio telemetry (default: {DEFAULT_RADIO_SERIAL_PORT})",
    )
    args = parser.parse_args()

    radio_serial_port = args.radio_port
    if radio_serial_port == DEFAULT_RADIO_SERIAL_PORT:
        print(f"WARNING: No --radio-port specified, using default: {DEFAULT_RADIO_SERIAL_PORT}")

    # Initialize overlay system
    telem_source = TelemetrySource(port=radio_serial_port, baud=57600)
    overlay_manager = OverlayManager()
    # TODO: add static overlay back in after test 
    # overlay_manager.add(StaticImageOverlay("overlay.png"))
    overlay_manager.add(TelemetryOverlay(source=telem_source))
    overlay_manager.add(GaugeOverlay(source=telem_source))
    overlay_manager.add(StatusOverlay())

    print("Starting video receiver...")
    print(f"Listening for stream on port {UDP_PORT}")
    print(f"Expected resolution: {FRAME_WIDTH}x{FRAME_HEIGHT}")

    # GStreamer pipeline - outputs raw BGR frames to stdout
    # max-size-buffers will buffer up to x before it starts to drop stuff => frames pile up in gstreamers queue and python reads them from the queue. make this small so frames are dropped if the python code isnt running fast enough
    gst_command = [
        'gst-launch-1.0.exe' if sys.platform == "win32" else 'gst-launch-1.0',
        '-q',
        'udpsrc',
        f'port={UDP_PORT}',
        'buffer-size=26214400',
        f'caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000',
        '!', 'rtpjitterbuffer',
        'latency=100',
        'drop-on-latency=true',
        '!', 'rtph264depay',
        '!', 'queue',
        'max-size-buffers=10',
        'max-size-bytes=0',
        'max-size-time=0',
        'leaky=downstream',
        '!', 'h264parse',
        '!', 'vaapih264dec',
        'error-resilient=true',
        '!', 'videoconvert',
        '!', f'video/x-raw,format=BGR,width={FRAME_WIDTH},height={FRAME_HEIGHT}',
        '!', 'fdsink',
        'sync=false',
    ]

    print("Starting GStreamer process...")

    frame_count = 0
    start_time = None
    end_reason = "Unknown"

    # Set up signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        nonlocal end_reason
        print(f"\nReceived signal {signum}, shutting down...")
        end_reason = f"Signal {signum} received"
        shutdown_flag.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Start GStreamer as subprocess
        process = subprocess.Popen(
            gst_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0
        )

        # Thread 1: reads raw frames from GStreamer → _latest_raw_frame
        reader_thread = threading.Thread(
            target=read_frames,
            args=(process,),
            daemon=True,
        )

        # Thread 2: composites overlays onto _latest_raw_frame → _latest_output_frame
        overlay_thread = threading.Thread(
            target=render_overlays,
            args=(overlay_manager,),
            daemon=True,
        )

        reader_thread.start()
        overlay_thread.start()

        print("Video stream opened successfully!")
        print("Press Ctrl+C to quit")

        camera = pyfakewebcam.FakeWebcam('/dev/video10', FRAME_WIDTH, FRAME_HEIGHT)

        start_time = datetime.now()
        frame_interval = 1.0 / VIDEO_FPS

        # Main loop: paces at VIDEO_FPS and pushes the latest composited frame
        # to the virtual camera.  This loop is intentionally decoupled from
        # both the GStreamer reader and the overlay renderer — it will keep
        # sending frames to pyfakewebcam even if either upstream thread stalls,
        # simply repeating the last good output frame.
        while not shutdown_flag.is_set():
            t0 = time.monotonic()

            with _output_frame_lock:
                frame = _latest_output_frame

            if frame is not None:
                frame_count += 1
                camera.schedule_frame(frame[:, :, ::-1])

            # Pace the output loop to VIDEO_FPS regardless of upstream speed
            elapsed = time.monotonic() - t0
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

            # Only stop if both upstream threads have exited
            if not reader_thread.is_alive() and not overlay_thread.is_alive():
                print("Both processing threads stopped")
                end_reason = "Reader and overlay threads both ended"
                break

    except KeyboardInterrupt:
        print("\nInterrupted by user")
        end_reason = "Keyboard interrupt (Ctrl+C)"
        shutdown_flag.set()

    except FileNotFoundError:
        print("ERROR: gst-launch-1.0 not found")
        print("Make sure GStreamer is installed and in your PATH")
        sys.exit(1)

    finally:
        end_time = datetime.now()
        shutdown_flag.set()  # Ensure flag is set for cleanup

        if 'reader_thread' in locals() and reader_thread.is_alive():
            print("Waiting for reader thread to finish...")
            reader_thread.join(timeout=2.0)

        if 'overlay_thread' in locals() and overlay_thread.is_alive():
            print("Waiting for overlay thread to finish...")
            overlay_thread.join(timeout=2.0)

        # Cleanup process
        if 'process' in locals():
            process.terminate()
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                print("Force killing GStreamer process...")
                process.kill()
                process.wait()

        telem_source.stop()
        print(f"Total frames displayed: {frame_count}")

        if start_time:
            duration = (end_time - start_time).total_seconds()
            actual_fps = frame_count / duration if duration > 0 else 0
            print(f"Duration: {duration:.2f}s, Actual FPS: {actual_fps:.2f}")

        print("Video receiver stopped")

if __name__ == "__main__":
    main()