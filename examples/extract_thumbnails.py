#!/usr/bin/env python3
"""
Extract thumbnails on keyframes using PyAV.

Requires:
    pip install av
"""

import os
import sys

try:
    import av  # type: ignore[import]
except ImportError:
    av = None  # pylint: disable=invalid-name

from sldp_grabber import SLDPGrabber


class ThumbnailExtractor:
    """Decode H.264 frames with PyAV and save thumbnails."""

    def __init__(self, out_dir="thumbnails"):
        os.makedirs(out_dir, exist_ok=True)
        self.out_dir = out_dir
        self.count = 0
        if av is not None:
            self.codec = av.CodecContext.create("h264", "r")
        else:
            self.codec = None

    def on_video_frame(self, data, meta):  # pylint: disable=unused-argument
        """Handle incoming video frame bytes."""
        if self.codec is None:
            return

        try:
            packet = av.packet.Packet(data)
            frames = self.codec.decode(packet)
        except av.AVError:  # type: ignore[attr-defined]
            return

        for frame in frames:
            self.count += 1
            path = os.path.join(self.out_dir, f"thumb_{self.count:04d}.jpg")
            frame.to_image().save(path)
            print(f"Saved {path}")


def main():
    """Record briefly and extract thumbnails."""
    if av is None:
        print("PyAV not installed. Run: pip install av")
        sys.exit(1)

    extractor = ThumbnailExtractor()

    grabber = SLDPGrabber(
        url="wss://.../stream",  # Replace with actual URL
        out_dir="thumb_recordings",
        duration=10,
        on_video_frame=extractor.on_video_frame,
        keep_raw=False,
    )

    print("Recording and extracting thumbnails with PyAV...")
    grabber.run(create_mp4=False)
    print(f"Saved {extractor.count} thumbnails")


if __name__ == "__main__":
    main()
