#!/usr/bin/env python3
"""
Extract thumbnails on keyframes using PyAV.
Requires: pip install av
"""

import os
import av
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sldp_grabber import SLDPGrabber


class ThumbnailExtractor:
    def __init__(self, out_dir="thumbnails"):
        os.makedirs(out_dir, exist_ok=True)
        self.out_dir = out_dir
        self.count = 0
        self.codec = av.CodecContext.create("h264", "r")

    def on_video_frame(self, data, meta):
        if not meta.get("keyframe", False):
            return

        try:
            packet = av.packet.Packet(data)
            frames = self.codec.decode(packet)
        except av.AVError:
            return

        for frame in frames:
            if not frame.key_frame:
                continue

            self.count += 1
            path = os.path.join(self.out_dir, f"thumb_{self.count:04d}.jpg")
            frame.to_image().save(path)
            print(f"Saved {path}")


def main():
    extractor = ThumbnailExtractor()

    grabber = SLDPGrabber(
        url="wss://.../stream",
        out_dir="thumb_recordings",
        duration=10,
        on_video_frame=extractor.on_video_frame,
        keep_raw=False,
    )

    print("Recording and extracting thumbnails with PyAV...")
    grabber.run(create_mp4=False)
    print(f"\nSaved {extractor.count} thumbnails")


if __name__ == "__main__":
    main()
