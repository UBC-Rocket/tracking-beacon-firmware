"""Shared telemetry data source that reads from serial."""

import csv
import os
import threading
import time

import serial
from datetime import datetime

from serial_decoder import decode_packet, flight_state_name, packet_to_csv_row, packet_to_dict, read_cobs_packet

# CSV column definitions
CSV_COLUMNS = [
    "recv_time",
    "counter",
    "timestamp_ms",
    "state",
    "accel_x",
    "accel_y",
    "accel_z",
    "gyro_x",
    "gyro_y",
    "gyro_z",
    "kf_altitude",
    "kf_velocity",
    "kf_alt_variance",
    "kf_vel_variance",
    "baro0_healthy",
    "baro1_healthy",
    "baro0_pressure",
    "baro0_temperature",
    "baro0_altitude",
    "baro0_nis",
    "baro0_faults",
    "baro1_pressure",
    "baro1_temperature",
    "baro1_altitude",
    "baro1_nis",
    "baro1_faults",
    "ground_altitude",
    "gps_latitude",
    "gps_longitude",
    "gps_altitude",
    "gps_speed",
    "gps_sats",
    "gps_fix",
]

TELEMETRY_DIR = "telemetry_logs"

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

        os.makedirs(TELEMETRY_DIR, exist_ok=True)

        self.csv_file = open(TELEMETRY_DIR + "/" + datetime.now().isoformat(timespec='milliseconds') + '.csv', 'w', newline='')
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow(CSV_COLUMNS)
        print(f"Logging to {self.csv_file.name}")

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
                        
                        self.csv_writer.writerow(packet_to_csv_row(packet))
                        self.csv_file.flush()  # Ensure data is written immediately

                        telemetry = packet_to_dict(packet)
                        with self._lock:
                            self._telemetry = telemetry
                            self._packet_count += 1
                            self._last_packet_time = time.monotonic()

                        W = 22  # column width

                        def _fmt(label, value):
                            return f"{label:<16}{value!s:<14}"

                        sep = "─" * (W * 3 + 2)

                        print(f"\n{'─'*6} Packet #{self._packet_count} {'─'*6}")
                        print(f"  {'counter':<16}{packet.counter:<14}  {'timestamp_ms':<16}{packet.timestamp_ms:<14}  {'state':<16}{flight_state_name(packet.state)}")
                        print(sep)

                        # IMU
                        print(f"  {'IMU':}")
                        print(f"  {'accel_x':<16}{packet.accel_x:<14.4f}  {'gyro_x':<16}{packet.gyro_x:<14.4f}  {'kf_altitude':<16}{packet.kf_altitude:.4f}")
                        print(f"  {'accel_y':<16}{packet.accel_y:<14.4f}  {'gyro_y':<16}{packet.gyro_y:<14.4f}  {'kf_velocity':<16}{packet.kf_velocity:.4f}")
                        print(f"  {'accel_z':<16}{packet.accel_z:<14.4f}  {'gyro_z':<16}{packet.gyro_z:<14.4f}  {'kf_alt_var':<16}{packet.kf_alt_variance:.4f}")
                        print(f"  {'':<16}{'':<14}  {'':<16}{'':<14}  {'kf_vel_var':<16}{packet.kf_vel_variance:.4f}")
                        print(sep)

                        # Barometers
                        print(f"  {'':16}  {'── Baro 0 ──':^30}  {'── Baro 1 ──':^30}")
                        print(f"  {'healthy':<16}  {str(packet.baro0_healthy):<30}  {str(packet.baro1_healthy):<30}")
                        print(f"  {'pressure':<16}  {packet.baro0_pressure:<30.2f}  {packet.baro1_pressure:<30.2f}")
                        print(f"  {'temp':<16}  {packet.baro0_temperature:<30.2f}  {packet.baro1_temperature:<30.2f}")
                        print(f"  {'altitude':<16}  {packet.baro0_altitude:<30.4f}  {packet.baro1_altitude:<30.4f}")
                        print(f"  {'nis':<16}  {packet.baro0_nis:<30.4f}  {packet.baro1_nis:<30.4f}")
                        print(f"  {'faults':<16}  {str(packet.baro0_faults):<30}  {str(packet.baro1_faults):<30}")
                        print(sep)

                        # GPS + Ground
                        print(f"  {'ground_alt':<16}{packet.ground_altitude:<14.4f}  {'gps_lat':<16}{packet.gps_latitude:<14.6f}  {'gps_lon':<16}{packet.gps_longitude:.6f}")
                        print(f"  {'gps_alt':<16}{packet.gps_altitude:<14.2f}  {'gps_speed':<16}{packet.gps_speed:<14.2f}  {'gps_sats':<16}{packet.gps_sats}")
                        print(f"  {'gps_fix':<16}{packet.gps_fix}")
                        print()

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
        """Stop the background reader thread and flush/close the CSV log."""
        self._stop_event.set()
        self._reader_thread.join(timeout=2.0)
        self.csv_file.flush()
        self.csv_file.close()