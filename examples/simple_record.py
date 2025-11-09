#!/usr/bin/env python3
"""
Minimal example: record a single SLDP stream.
"""

from sldp_grabber import SLDPGrabber


def main():
    """Record for 10 seconds."""
    grabber = SLDPGrabber(
        url="wss://.../stream",  # Replace with real URL
        out_dir="recordings",
        duration=10,
    )
    grabber.run()


if __name__ == "__main__":
    main()
