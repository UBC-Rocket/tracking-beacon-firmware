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

                                                # Full output - all fields
                        print(f"[{self._packet_count}] TelemetryPacket:")
                        print(f"    counter:         {packet.counter}")
                        print(f"    timestamp_ms:    {packet.timestamp_ms}")
                        print(f"    state:           {flight_state_name(packet.state)}")
                        print(f"    accel_x:         {packet.accel_x:.4f}")
                        print(f"    accel_y:         {packet.accel_y:.4f}")
                        print(f"    accel_z:         {packet.accel_z:.4f}")
                        print(f"    gyro_x:          {packet.gyro_x:.4f}")
                        print(f"    gyro_y:          {packet.gyro_y:.4f}")
                        print(f"    gyro_z:          {packet.gyro_z:.4f}")
                        print(f"    kf_altitude:     {packet.kf_altitude:.4f}")
                        print(f"    kf_velocity:     {packet.kf_velocity:.4f}")
                        print(f"    kf_alt_variance: {packet.kf_alt_variance:.4f}")
                        print(f"    kf_vel_variance: {packet.kf_vel_variance:.4f}")
                        print(f"    baro0_healthy:   {packet.baro0_healthy}")
                        print(f"    baro0_pressure:  {packet.baro0_pressure:.2f}")
                        print(f"    baro0_temp:      {packet.baro0_temperature:.2f}")
                        print(f"    baro0_altitude:  {packet.baro0_altitude:.4f}")
                        print(f"    baro0_nis:       {packet.baro0_nis:.4f}")
                        print(f"    baro0_faults:    {packet.baro0_faults}")
                        print(f"    baro1_healthy:   {packet.baro1_healthy}")
                        print(f"    baro1_pressure:  {packet.baro1_pressure:.2f}")
                        print(f"    baro1_temp:      {packet.baro1_temperature:.2f}")
                        print(f"    baro1_altitude:  {packet.baro1_altitude:.4f}")
                        print(f"    baro1_nis:       {packet.baro1_nis:.4f}")
                        print(f"    baro1_faults:    {packet.baro1_faults}")
                        print(f"    ground_altitude: {packet.ground_altitude:.4f}")
                        print(f"    gps_latitude:    {packet.gps_latitude:.6f}")
                        print(f"    gps_longitude:   {packet.gps_longitude:.6f}")
                        print(f"    gps_altitude:    {packet.gps_altitude:.2f}")
                        print(f"    gps_speed:       {packet.gps_speed:.2f}")
                        print(f"    gps_sats:        {packet.gps_sats}")
                        print(f"    gps_fix:         {packet.gps_fix}")
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
        """Stop the background reader thread."""
        self._stop_event.set()
        self._reader_thread.join(timeout=2.0)
