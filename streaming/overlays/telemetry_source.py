"""Shared telemetry data source that reads from serial."""

import threading
import time

import serial

from serial_decoder import decode_packet, packet_to_dict, read_cobs_packet


class TelemetrySource:
    """
    Background serial reader that decodes COBS/CRC/protobuf telemetry packets.

    Multiple overlays can share a single TelemetrySource instance to read
    from the same serial stream without duplicating the reader thread.
    """

    def __init__(self, port, baud=57600, timeout=1.0):
        self._lock = threading.Lock()
        self._telemetry = {}
        self._connected = False
        self._packet_count = 0
        self._error_count = 0
        self._last_packet_time = 0.0
        self._stale_threshold = 2.0

        self._port = port
        self._baud = baud
        self._timeout = timeout

        self._stop_event = threading.Event()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def _reader_loop(self):
        """Background thread that reads and decodes serial telemetry."""
        while not self._stop_event.is_set():
            try:
                with serial.Serial(self._port, self._baud,
                                   timeout=self._timeout) as ser:
                    with self._lock:
                        self._connected = True

                    while not self._stop_event.is_set():
                        raw_data = read_cobs_packet(ser)
                        if raw_data is None:
                            continue

                        packet = decode_packet(raw_data)
                        if packet is None:
                            with self._lock:
                                self._error_count += 1
                            continue

                        telemetry = packet_to_dict(packet)
                        with self._lock:
                            self._telemetry = telemetry
                            self._packet_count += 1
                            self._last_packet_time = time.monotonic()

            except serial.SerialException:
                with self._lock:
                    self._connected = False
                self._stop_event.wait(2.0)

    def get(self):
        """
        Return a snapshot of the current telemetry state.

        Returns:
            dict with keys: telemetry, connected, packet_count,
            error_count, last_packet_time, stale
        """
        with self._lock:
            now = time.monotonic()
            stale = (now - self._last_packet_time) > self._stale_threshold if self._last_packet_time else False
            return {
                "telemetry": self._telemetry.copy(),
                "connected": self._connected,
                "packet_count": self._packet_count,
                "error_count": self._error_count,
                "last_packet_time": self._last_packet_time,
                "stale": stale,
            }

    def stop(self):
        """Stop the background reader thread."""
        self._stop_event.set()
        self._reader_thread.join(timeout=2.0)
