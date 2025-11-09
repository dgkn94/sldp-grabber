#!/usr/bin/env python3
"""
Example: live preview while recording using ffplay.
"""

from sldp_grabber import SLDPGrabber


def main():
    """Run grabber with live H.264 preview."""
    grabber = SLDPGrabber(
        url="wss://.../stream",  # Replace with actual URL
        out_dir="preview_recordings",
        duration=60,
        pipe_h264_cmd=["ffplay", "-fflags", "nobuffer", "-flags", "low_delay", "-f", "h264", "-i", "-"],
        keep_raw=True,
    )

    print("Starting recording with live preview...")
    print("Press Ctrl+C to stop")
    grabber.run()
    print("Recording complete!")


if __name__ == "__main__":
    main()
