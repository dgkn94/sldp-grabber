#!/usr/bin/env python3
"""
Example: using custom frame processors with SLDPGrabber.
"""

from sldp_grabber import SLDPGrabber


class FrameProcessor:
    """Simple processor that counts frames."""

    def __init__(self):
        self.video_count = 0
        self.audio_count = 0

    def on_video_frame(self, data, meta):
        """Called for each video frame."""
        del meta  # or use it if you like
        self.video_count += 1
        if self.video_count % 30 == 0:
            print(
                f"Video frame {self.video_count}: {len(data)} bytes"
            )

    def on_audio_frame(self, data, meta):
        """Called for each audio frame."""
        del meta
        self.audio_count += 1
        if self.audio_count % 100 == 0:
            print(f"Audio frame {self.audio_count}: {len(data)} bytes")


def main():
    """Run a short capture with the custom processor."""
    processor = FrameProcessor()

    grabber = SLDPGrabber(
        url="wss://.../stream",  # Replace with actual URL
        out_dir="processed_recordings",
        duration=10,
        on_video_frame=processor.on_video_frame,
        on_audio_frame=processor.on_audio_frame,
    )

    print("Starting recording with custom processor...")
    grabber.run()
    print(
        f"Processed {processor.video_count} video frames and "
        f"{processor.audio_count} audio frames"
    )


if __name__ == "__main__":
    main()
