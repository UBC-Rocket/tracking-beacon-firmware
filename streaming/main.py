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

def read_frames(process, frame_queue):
    """Read raw video frames from GStreamer subprocess"""
    while not shutdown_flag.is_set():
        try:
            raw_frame = process.stdout.read(FRAME_SIZE)
            if len(raw_frame) != FRAME_SIZE:
                print("Stream ended or incomplete frame")
                shutdown_flag.set()
                break

            # Convert raw bytes to numpy array (copy to make it writable)
            frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape((FRAME_HEIGHT, FRAME_WIDTH, 3)).copy()

            # Put frame in display queue, dropping oldest frame if full
            # This ensures the main loop always gets the most recent frames
            try:
                frame_queue.put_nowait(frame)
            except queue.Full:
                try:
                    frame_queue.get_nowait()  # discard oldest
                except queue.Empty:
                    pass
                frame_queue.put_nowait(frame)

        except Exception as e:
            print(f"Error reading frame: {e}")
            break


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

        # Create queue for frames
        frame_queue = queue.Queue(maxsize=5)

        # Start reader thread
        reader_thread = threading.Thread(
            target=read_frames,
            args=(process, frame_queue),
            daemon=True
        )
        reader_thread.start()

        print("Video stream opened successfully!")
        print("Press Ctrl+C to quit")

        # Create window
        # window_name = 'Drone Video Feed'
        # cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        # cv2.resizeWindow(window_name, 1280, 720)
        camera = pyfakewebcam.FakeWebcam('/dev/video10', FRAME_WIDTH, FRAME_HEIGHT)


        start_time = datetime.now()

        while not shutdown_flag.is_set():
            try:
                # Get frame from display queue (short timeout to stay responsive)
                frame = frame_queue.get(timeout=0.1)

                frame_count += 1

                # Apply overlays to frame
                context = {"frame_count": frame_count, "recording": True}
                frame = overlay_manager.render(frame, context)

                # Display the frame (swap BGR→RGB via numpy slice, no copy)
                camera.schedule_frame(frame[:, :, ::-1])

            except queue.Empty:
                # No frame received in timeout period
                if not reader_thread.is_alive():
                    print("Frame reader thread stopped")
                    end_reason = "Stream ended or connection lost"
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

        # Give threads time to finish
        if 'reader_thread' in locals() and reader_thread.is_alive():
            print("Waiting for reader thread to finish...")
            reader_thread.join(timeout=2.0)

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
