"""SpaceX-style arc gauge overlay for speed and altitude."""

import math

import cv2
import numpy as np

from .base import OverlayBase


# Colors (BGR)
WHITE = (255, 255, 255)
LIGHT_GREY = (180, 180, 180)
DARK_GREY = (60, 60, 60)
RED = (0, 0, 255)
BG_COLOR = (20, 20, 20)


class GaugeOverlay(OverlayBase):
    """
    Draws two SpaceX-style arc gauges (speed + altitude) at the bottom
    center of the video frame, reading from a shared TelemetrySource.
    """

    def __init__(self, source, enabled=True, radius=90, arc_thickness=10,
                 gap=60, y_offset=120, max_speed=500, max_altitude=10000,
                 label="TELEMETRY"):
        super().__init__(enabled)
        self.source = source
        self.radius = radius
        self.arc_thickness = arc_thickness
        self.gap = gap
        self.y_offset = y_offset  # distance from bottom of frame
        self.max_speed = max_speed
        self.max_altitude = max_altitude
        self.label = label

        # Arc sweep: 270 degrees, starting from bottom-left going clockwise
        # OpenCV ellipse angles: 0 = 3 o'clock, goes clockwise
        # We want the gap at the bottom, so sweep from 135 to 405 (i.e. 135 to 45 next rotation)
        self.arc_start = 135  # bottom-left
        self.arc_end = 405    # bottom-right (135 + 270)
        self.arc_sweep = self.arc_end - self.arc_start  # 270 degrees

        self.font = cv2.FONT_HERSHEY_SIMPLEX
        self.font_small = cv2.FONT_HERSHEY_SIMPLEX

    def _draw_gauge(self, frame, center, value, max_value, title, unit):
        """Draw a single arc gauge at the given center position."""
        cx, cy = center
        r = self.radius
        t = self.arc_thickness

        # Clamp value
        clamped = max(0, min(value, max_value))
        fill_fraction = clamped / max_value if max_value > 0 else 0
        fill_angle = self.arc_start + fill_fraction * self.arc_sweep

        # Background arc (dark grey track)
        cv2.ellipse(frame, (cx, cy), (r, r), 0,
                    self.arc_start, self.arc_end,
                    DARK_GREY, t, cv2.LINE_AA)

        # Filled arc (white, proportional to value)
        if fill_fraction > 0.001:
            cv2.ellipse(frame, (cx, cy), (r, r), 0,
                        self.arc_start, fill_angle,
                        WHITE, t, cv2.LINE_AA)

            # Red needle tick at the fill point
            tick_half = 3  # degrees
            cv2.ellipse(frame, (cx, cy), (r, r), 0,
                        fill_angle - tick_half, fill_angle + tick_half,
                        RED, t + 4, cv2.LINE_AA)

        # Title label above gauge (e.g. "SPEED")
        title_scale = 0.45
        title_thickness = 1
        (tw, th), _ = cv2.getTextSize(title, self.font, title_scale, title_thickness)
        cv2.putText(frame, title, (cx - tw // 2, cy - r - 12),
                    self.font, title_scale, LIGHT_GREY, title_thickness, cv2.LINE_AA)

        # Value text (large, centered)
        if value >= 1000:
            value_text = f"{value:.0f}"
        elif value >= 100:
            value_text = f"{value:.0f}"
        elif value >= 10:
            value_text = f"{value:.1f}"
        else:
            value_text = f"{value:.2f}"

        value_scale = 0.9
        value_thickness = 2
        (vw, vh), _ = cv2.getTextSize(value_text, self.font, value_scale, value_thickness)
        cv2.putText(frame, value_text, (cx - vw // 2, cy + vh // 2 - 2),
                    self.font, value_scale, WHITE, value_thickness, cv2.LINE_AA)

        # Unit label below value (e.g. "M/S")
        unit_scale = 0.4
        unit_thickness = 1
        (uw, uh), _ = cv2.getTextSize(unit, self.font_small, unit_scale, unit_thickness)
        cv2.putText(frame, unit, (cx - uw // 2, cy + vh // 2 + uh + 8),
                    self.font_small, unit_scale, LIGHT_GREY, unit_thickness, cv2.LINE_AA)

    def render(self, frame, context=None):
        """Render speed and altitude gauges onto frame."""
        h, w = frame.shape[:2]

        snapshot = self.source.get()
        telem = snapshot["telemetry"]

        speed = abs(telem.get("kf_velocity", 0))
        altitude = telem.get("kf_altitude", 0)

        # Gauge centers: side-by-side at bottom center of frame
        center_y = h - self.y_offset
        left_cx = w // 2 - self.radius - self.gap // 2
        right_cx = w // 2 + self.radius + self.gap // 2

        # Semi-transparent dark background behind gauges
        bg_w = (self.radius * 2 + self.gap) * 2 + 40
        bg_h = self.radius * 2 + 80
        bg_x1 = w // 2 - bg_w // 2
        bg_y1 = center_y - self.radius - 30
        bg_x2 = bg_x1 + bg_w
        bg_y2 = bg_y1 + bg_h

        # Alpha-blend the background rectangle
        overlay = frame.copy()
        cv2.rectangle(overlay, (bg_x1, bg_y1), (bg_x2, bg_y2), BG_COLOR, -1)
        cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

        # Draw gauges
        self._draw_gauge(frame, (left_cx, center_y), speed, self.max_speed, "SPEED", "M/S")
        self._draw_gauge(frame, (right_cx, center_y), altitude, self.max_altitude, "ALTITUDE", "M")

        # Footer label centered below both gauges
        label_scale = 0.5
        label_thickness = 1
        (lw, lh), _ = cv2.getTextSize(self.label, self.font, label_scale, label_thickness)
        cv2.putText(frame, self.label, (w // 2 - lw // 2, center_y + self.radius + 25),
                    self.font, label_scale, LIGHT_GREY, label_thickness, cv2.LINE_AA)

        return frame
