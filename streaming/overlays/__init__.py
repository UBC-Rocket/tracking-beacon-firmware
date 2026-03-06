"""
Overlay system for video feed.

Provides a modular way to stack multiple graphics layers on top of video frames.
"""

from .base import OverlayBase
from .gauge import GaugeOverlay
from .static_image import StaticImageOverlay
from .status import StatusOverlay
from .telemetry import TelemetryOverlay
from .telemetry_source import TelemetrySource


class OverlayManager:
    """Manages multiple overlays, rendering them in order."""

    def __init__(self):
        self.overlays = []  # Ordered list, rendered bottom to top

    def add(self, overlay):
        """Add an overlay to the stack."""
        if not isinstance(overlay, OverlayBase):
            raise TypeError("Overlay must inherit from OverlayBase")
        self.overlays.append(overlay)

    def remove(self, overlay):
        """Remove an overlay from the stack."""
        self.overlays.remove(overlay)

    def render(self, frame, context=None):
        """Render all enabled overlays onto the frame."""
        for overlay in self.overlays:
            if overlay.enabled:
                frame = overlay.render(frame, context)
        return frame


__all__ = [
    "GaugeOverlay",
    "OverlayBase",
    "OverlayManager",
    "StaticImageOverlay",
    "StatusOverlay",
    "TelemetryOverlay",
    "TelemetrySource",
]
