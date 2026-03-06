#!/usr/bin/env python3
"""
Simple UDP video stream receiver and display
Receives H264/H265 stream from cosmostreamer and displays it in a window
Records video continuously to disk as frames arrive
"""

import argparse
import cv2
import numpy as np
import subprocess
import sys
import threading
import queue
import os
import signal
from datetime import datetime

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
CAPTURES_DIR = "captures"
VIDEO_FPS = 30  # Adjust based on your stream's FPS
DEFAULT_RADIO_SERIAL_PORT = "/tmp/telem_rx"

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

            # Put frame in display queue (non-blocking, drop old frames if queue is full)
            try:
                frame_queue.put_nowait(frame)
            except queue.Full:
                pass  # Drop frame if display queue is full

        except Exception as e:
            print(f"Error reading frame: {e}")
            break


def write_frames(recording_queue, video_writer, frame_counter):
    """Background thread that writes frames to disk continuously."""
    while not shutdown_flag.is_set():
        try:
            frame = recording_queue.get(timeout=0.5)
            video_writer.write(frame)
            frame_counter['count'] += 1
        except queue.Empty:
            continue


def main():
    global shutdown_flag

    parser = argparse.ArgumentParser(description="UDP video stream receiver and display")
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
    print(f"Listening for UDP stream on port {UDP_PORT}")
    print(f"Expected resolution: {FRAME_WIDTH}x{FRAME_HEIGHT}")

    # GStreamer pipeline - outputs raw BGR frames to stdout
    gst_command = [
        'gst-launch-1.0.exe' if os.name == 'nt' else 'gst-launch-1.0',
        '-q',  # Quiet mode
        'udpsrc',
        f'port={UDP_PORT}',
        'buffer-size=13000000',
        '!', 'parsebin',
        '!', 'decodebin',
        '!', 'videoconvert',
        '!', f'video/x-raw,format=BGR,width={FRAME_WIDTH},height={FRAME_HEIGHT}',
        '!', 'fdsink',  # Output to stdout (file descriptor sink)
    ]

    print("Starting GStreamer process...")

    frame_count = 0
    start_time = None
    end_reason = "Unknown"

    # Set up recording output
    os.makedirs(CAPTURES_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    video_filename = os.path.join(CAPTURES_DIR, f"capture_{timestamp}.mp4")
    metadata_filename = os.path.join(CAPTURES_DIR, f"capture_{timestamp}.txt")

    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    video_writer = cv2.VideoWriter(video_filename, fourcc, VIDEO_FPS,
                                   (FRAME_WIDTH, FRAME_HEIGHT))

    recorded_counter = {'count': 0}

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
            bufsize=FRAME_SIZE
        )

        # Create queues for frames
        frame_queue = queue.Queue(maxsize=5)
        recording_queue = queue.Queue(maxsize=60)  # ~2s buffer at 30fps

        # Start threads
        reader_thread = threading.Thread(
            target=read_frames,
            args=(process, frame_queue),
            daemon=True
        )
        writer_thread = threading.Thread(
            target=write_frames,
            args=(recording_queue, video_writer, recorded_counter),
            daemon=True
        )
        reader_thread.start()
        writer_thread.start()

        print("Video stream opened successfully!")
        print(f"Recording to {video_filename}")
        print("Press 'q' to quit, or close the window")

        # Create window
        window_name = 'Drone Video Feed'
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, 1280, 720)

        start_time = datetime.now()

        while not shutdown_flag.is_set():
            try:
                # Get frame from display queue (short timeout to stay responsive)
                frame = frame_queue.get(timeout=0.1)

                frame_count += 1

                # Apply overlays to frame
                context = {"frame_count": frame_count, "recording": True}
                frame = overlay_manager.render(frame, context)

                # Send overlaid frame to recording
                try:
                    recording_queue.put_nowait(frame)
                except queue.Full:
                    pass  # Drop frame rather than filling memory

                # Display the frame
                cv2.imshow(window_name, frame)

            except queue.Empty:
                # No frame received in timeout period
                if not reader_thread.is_alive():
                    print("Frame reader thread stopped")
                    end_reason = "Stream ended or connection lost"
                    break

            # Check for 'q' key or window close (must call waitKey for window events)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                print("Quit signal received (pressed 'q')")
                end_reason = "User quit (pressed 'q')"
                shutdown_flag.set()
                break

            # Check if window was closed via X button
            try:
                if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                    print("Window closed by user")
                    end_reason = "Window closed (X button)"
                    shutdown_flag.set()
                    break
            except cv2.error:
                print("Window no longer exists")
                end_reason = "Window closed"
                shutdown_flag.set()
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

        if 'writer_thread' in locals() and writer_thread.is_alive():
            print("Flushing remaining frames to disk...")
            writer_thread.join(timeout=5.0)

        # Cleanup process
        if 'process' in locals():
            process.terminate()
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                print("Force killing GStreamer process...")
                process.kill()
                process.wait()

        # Close video writer (finalizes the mp4 file)
        video_writer.release()

        telem_source.stop()
        cv2.destroyAllWindows()
        print(f"Total frames displayed: {frame_count}")
        print(f"Total frames recorded: {recorded_counter['count']}")

        # Save metadata
        if start_time:
            duration = (end_time - start_time).total_seconds()
            actual_fps = recorded_counter['count'] / duration if duration > 0 else 0
            file_size = os.path.getsize(video_filename) if os.path.exists(video_filename) else 0

            metadata = f"""Capture Metadata
================
Filename: {os.path.basename(video_filename)}
Start Time: {start_time.strftime("%Y-%m-%d %H:%M:%S")}
End Time: {end_time.strftime("%Y-%m-%d %H:%M:%S")}
Duration: {duration:.2f} seconds
Total Frames: {recorded_counter['count']}
Displayed Frames: {frame_count}
Resolution: {FRAME_WIDTH}x{FRAME_HEIGHT}
Target FPS: {VIDEO_FPS}
Actual FPS: {actual_fps:.2f}
File Size: {file_size / (1024*1024):.2f} MB
UDP Port: {UDP_PORT}
End Reason: {end_reason}
"""
            with open(metadata_filename, 'w') as f:
                f.write(metadata)
            print(f"Recording saved: {video_filename} ({file_size / (1024*1024):.1f} MB)")

        print("Video receiver stopped")

if __name__ == "__main__":
    main()
