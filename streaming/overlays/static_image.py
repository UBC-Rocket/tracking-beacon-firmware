"""Static image overlay with alpha channel support."""

import cv2
import numpy as np

from .base import OverlayBase


class StaticImageOverlay(OverlayBase):
    """Handles PNG overlays with alpha channel transparency."""

    def __init__(self, image_path, enabled=True):
        super().__init__(enabled)
        self.image_path = image_path
        self.overlay_bgr = None
        self.alpha_channel = None
        self._load_image()

    def _load_image(self):
        """Load image with alpha channel preserved."""
        # Load with IMREAD_UNCHANGED to preserve alpha channel
        image = cv2.imread(self.image_path, cv2.IMREAD_UNCHANGED)

        if image is None:
            print(f"Warning: Could not load overlay image: {self.image_path}")
            return

        if image.shape[2] == 4:
            # Image has alpha channel (BGRA)
            self.overlay_bgr = image[:, :, :3]
            self.alpha_channel = image[:, :, 3]
        else:
            # No alpha channel — overlaying this would cover the entire video
            # feed with an opaque image. Skip it and warn instead.
            print(f"Warning: Overlay image '{self.image_path}' has no alpha channel "
                  f"and will not be applied (would cover the full video frame).")
            return

        print(f"Loaded overlay: {self.image_path} ({image.shape[1]}x{image.shape[0]})")

    def render(self, frame, context=None):
        """Apply overlay to frame using alpha compositing."""
        if self.alpha_channel is None or self.overlay_bgr is None:
            return frame

        # Check if overlay needs resizing to match frame
        if (self.overlay_bgr.shape[0] != frame.shape[0] or
                self.overlay_bgr.shape[1] != frame.shape[1]):
            # Resize overlay to match frame dimensions
            self.overlay_bgr = cv2.resize(self.overlay_bgr,
                                          (frame.shape[1], frame.shape[0]),
                                          interpolation=cv2.INTER_AREA)
            self.alpha_channel = cv2.resize(self.alpha_channel,
                                            (frame.shape[1], frame.shape[0]),
                                            interpolation=cv2.INTER_AREA)

        # Alpha compositing: blend overlay onto frame
        # Where alpha > 0, blend the overlay; where alpha == 0, keep original
        alpha = self.alpha_channel.astype(np.float32) / 255.0
        alpha_3ch = np.dstack([alpha, alpha, alpha])

        # Blend: output = (1 - alpha) * frame + alpha * overlay
        frame = ((1 - alpha_3ch) * frame + alpha_3ch * self.overlay_bgr).astype(np.uint8)

        return frame