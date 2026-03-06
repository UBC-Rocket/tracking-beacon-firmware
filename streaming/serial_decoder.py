"""
Serial decoder for COBS-encoded, CRC-protected protobuf packets.

Packet format (before COBS encoding):
    [protobuf_data][crc16_le (2 bytes)]

COBS framing:
    - Packets are COBS encoded
    - 0x00 byte is used as packet delimiter
"""

import sys
from math import sqrt

import crcmod
import serial
from cobs import cobs
from google.protobuf.message import DecodeError
from datetime import datetime

from TelemetryPacket_pb2 import FlightState, TelemetryPacket

# CRC-16-CCITT Kermit variant (polynomial 0x1021, init 0x0000, reflected)
crc16_func = crcmod.predefined.mkCrcFun('kermit')


def decode_packet(raw_data: bytes) -> TelemetryPacket | None:
    """
    Decode a COBS-encoded, CRC-protected protobuf packet.

    Args:
        raw_data: COBS-encoded data (without the trailing 0x00 delimiter)

    Returns:
        Decoded TelemetryPacket or None if decoding failed
    """
    try:
        decoded = cobs.decode(raw_data)
    except cobs.DecodeError as e:
        print(f"[ERROR] COBS decode failed: {e}", file=sys.stderr)
        return None

    if len(decoded) < 2:
        print(f"[ERROR] Packet too short ({len(decoded)} bytes)", file=sys.stderr)
        return None

    payload = decoded[:-2]
    crc_bytes = decoded[-2:]
    received_crc = int.from_bytes(crc_bytes, byteorder='little')

    computed_crc = crc16_func(payload)
    if computed_crc != received_crc:
        print(f"[WARNING] CRC mismatch! Received: 0x{received_crc:04X}, "
              f"Computed: 0x{computed_crc:04X}", file=sys.stderr)

    try:
        packet = TelemetryPacket()
        packet.ParseFromString(payload)
        return packet
    except DecodeError as e:
        print(f"[ERROR] Protobuf decode failed: {e}", file=sys.stderr)
        return None


def read_cobs_packet(ser: serial.Serial) -> bytes | None:
    """
    Read a complete COBS packet from serial (delimited by 0x00).

    Returns:
        The raw COBS-encoded data (without delimiter), or None on timeout/error
    """
    buffer = bytearray()

    while True:
        byte = ser.read(1)
        if not byte:
            if buffer:
                print(f"[WARNING] Timeout with {len(buffer)} bytes in buffer",
                      file=sys.stderr)
            return None

        if byte[0] == 0x00:
            if buffer:
                return bytes(buffer)
            continue

        buffer.append(byte[0])

        if len(buffer) > 4096:
            print("[ERROR] Buffer overflow, discarding", file=sys.stderr)
            buffer.clear()

def flight_state_name(state: FlightState) -> str:
    """Convert FlightState enum to human-readable string."""
    names = {
        FlightState.STANDBY: "STANDBY",
        FlightState.ASCENT: "ASCENT",
        FlightState.MACH_LOCK: "MACH_LOCK",
        FlightState.DROGUE_DESCENT: "DROGUE_DESCENT",
        FlightState.MAIN_DESCENT: "MAIN_DESCENT",
        FlightState.LANDED: "LANDED",
    }
    return names.get(state, f"UNKNOWN({state})")

def packet_to_dict(packet: TelemetryPacket) -> dict:
    """Convert a TelemetryPacket to a plain dict for overlay consumption."""
    accel_mag = sqrt(packet.accel_x**2 + packet.accel_y**2 + packet.accel_z**2)
    return {
        "counter": packet.counter,
        "timestamp_ms": packet.timestamp_ms,
        "state": flight_state_name(packet.state),
        "accel_x": packet.accel_x,
        "accel_y": packet.accel_y,
        "accel_z": packet.accel_z,
        "accel_magnitude": accel_mag,
        "gyro_x": packet.gyro_x,
        "gyro_y": packet.gyro_y,
        "gyro_z": packet.gyro_z,
        "kf_altitude": packet.kf_altitude,
        "kf_velocity": packet.kf_velocity,
        "kf_alt_variance": packet.kf_alt_variance,
        "kf_vel_variance": packet.kf_vel_variance,
        "baro0_healthy": packet.baro0_healthy,
        "baro1_healthy": packet.baro1_healthy,
        "baro0_pressure": packet.baro0_pressure,
        "baro0_temperature": packet.baro0_temperature,
        "baro0_altitude": packet.baro0_altitude,
        "baro1_pressure": packet.baro1_pressure,
        "baro1_temperature": packet.baro1_temperature,
        "baro1_altitude": packet.baro1_altitude,
        "ground_altitude": packet.ground_altitude,
        "gps_latitude": packet.gps_latitude,
        "gps_longitude": packet.gps_longitude,
        "gps_altitude": packet.gps_altitude,
        "gps_speed": packet.gps_speed,
        "gps_sats": packet.gps_sats,
        "gps_fix": packet.gps_fix,
    }

def packet_to_csv_row(packet: TelemetryPacket) -> list:
    """Convert a TelemetryPacket to a CSV row."""
    return [
        datetime.now().isoformat(timespec='milliseconds'),
        packet.counter,
        packet.timestamp_ms,
        flight_state_name(packet.state),
        packet.accel_x,
        packet.accel_y,
        packet.accel_z,
        packet.gyro_x,
        packet.gyro_y,
        packet.gyro_z,
        packet.kf_altitude,
        packet.kf_velocity,
        packet.kf_alt_variance,
        packet.kf_vel_variance,
        packet.baro0_healthy,
        packet.baro1_healthy,
        packet.baro0_pressure,
        packet.baro0_temperature,
        packet.baro0_altitude,
        packet.baro0_nis,
        packet.baro0_faults,
        packet.baro1_pressure,
        packet.baro1_temperature,
        packet.baro1_altitude,
        packet.baro1_nis,
        packet.baro1_faults,
        packet.ground_altitude,
        packet.gps_latitude,
        packet.gps_longitude,
        packet.gps_altitude,
        packet.gps_speed,
        packet.gps_sats,
        packet.gps_fix,
    ]