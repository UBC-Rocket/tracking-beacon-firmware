"""Telemetry overlay for displaying live rocket data."""

import time

import cv2

from .base import OverlayBase


# Colors (BGR)
WHITE = (255, 255, 255)
GREEN = (0, 255, 0)
RED = (0, 0, 255)
YELLOW = (0, 255, 255)
CYAN = (255, 255, 0)
BG = (0, 0, 0)

STATE_COLORS = {
    "STANDBY": WHITE,
    "ASCENT": GREEN,
    "MACH_LOCK": YELLOW,
    "DROGUE_DESCENT": CYAN,
    "MAIN_DESCENT": CYAN,
    "LANDED": WHITE,
}


class TelemetryOverlay(OverlayBase):
    """
    Renders a HUD overlay of live rocket telemetry onto the video frame.

    Reads data from a shared TelemetrySource instance.
    """

    def __init__(self, source, enabled=True,
                 position=(10, 80), font_scale=0.7, line_spacing=30):
        super().__init__(enabled)
        self.source = source
        self.position = position
        self.font_scale = font_scale
        self.line_spacing = line_spacing

        self.font = cv2.FONT_HERSHEY_SIMPLEX
        self.thickness = 2

    def _draw_text(self, frame, text, position, color=WHITE):
        """Draw text with a black background for readability."""
        (tw, th), baseline = cv2.getTextSize(
            text, self.font, self.font_scale, self.thickness
        )
        x, y = position
        pad = 4
        cv2.rectangle(frame, (x - pad, y - th - pad),
                       (x + tw + pad, y + baseline + pad), BG, -1)
        cv2.putText(frame, text, position, self.font, self.font_scale,
                    color, self.thickness)

    def render(self, frame, context=None):
        """Render telemetry HUD onto frame."""
        snapshot = self.source.get()
        telem = snapshot["telemetry"]
        connected = snapshot["connected"]
        pkt_count = snapshot["packet_count"]
        err_count = snapshot["error_count"]
        last_time = snapshot["last_packet_time"]
        stale = snapshot["stale"]

        x, y = self.position
        dy = self.line_spacing

        if not connected:
            self._draw_text(frame, "TELEMETRY: NO SERIAL", (x, y), RED)
            return frame

        if not telem:
            self._draw_text(frame, "TELEMETRY: WAITING...", (x, y), YELLOW)
            return frame

        state = telem.get("state", "UNKNOWN")
        state_color = STATE_COLORS.get(state, WHITE)

        if stale:
            age = time.monotonic() - last_time
            self._draw_text(frame, f"TELEMETRY STALE ({age:.0f}s)", (x, y), RED)
            y += dy

        lines = [
            (f"{state}", state_color if not stale else YELLOW),
            (f"ALT  {telem.get('kf_altitude', 0):>8.1f} m", WHITE),
            (f"VEL  {telem.get('kf_velocity', 0):>8.1f} m/s", WHITE),
            (f"ACC  {telem.get('accel_magnitude', 0):>8.1f} m/s2", WHITE),
            (f"GPS  {telem.get('gps_latitude', 0):.5f}, "
             f"{telem.get('gps_longitude', 0):.5f}", CYAN),
            (f"SATS {telem.get('gps_sats', 0)}  "
             f"FIX {telem.get('gps_fix', 0)}", CYAN),
            (f"BARO {'OK' if telem.get('baro0_healthy') else 'FAIL'} / "
             f"{'OK' if telem.get('baro1_healthy') else 'FAIL'}",
             GREEN if telem.get('baro0_healthy') and telem.get('baro1_healthy') else RED),
            (f"T+{telem.get('timestamp_ms', 0) / 1000:.1f}s  "
             f"PKT #{pkt_count}"
             + (f"  ERR {err_count}" if err_count else ""),
             RED if err_count else WHITE),
        ]

        for i, (text, color) in enumerate(lines):
            self._draw_text(frame, text, (x, y + i * dy), color)

        return frame
