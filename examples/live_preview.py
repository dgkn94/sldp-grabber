#!/usr/bin/env python3
"""
Example of live preview while recording
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sldp_grabber import SLDPGrabber

def main():
    # This will show live preview in ffplay while also saving to file
    grabber = SLDPGrabber(
        url="wss://.../stream",  # Replace with actual URL
        out_dir="preview_recordings",
        duration=60,
        pipe_h264_cmd=["ffplay", "-f", "h264", "-i", "-"],
        keep_raw=True
    )
    
    print("Starting recording with live preview...")
    print("Press Ctrl+C in the ffplay window or here to stop")
    grabber.run()
    print("Recording complete!")

if __name__ == "__main__":
    main()