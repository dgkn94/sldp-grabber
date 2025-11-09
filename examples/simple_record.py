#!/usr/bin/env python3
"""
Record a single SLDP stream to MP4 (if possible).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sldp_grabber import SLDPGrabber


def main():
    grabber = SLDPGrabber(
        url="wss://.../stream",       # Replace
        out_dir="recordings_simple",
        duration=30,                  # 30 seconds
    )

    print("Recording 30 seconds...")
    grabber.run(create_mp4=True)     # Uses ffmpeg if available
    print("Done.")


if __name__ == "__main__":
    main()
