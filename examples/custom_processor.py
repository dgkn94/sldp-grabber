#!/usr/bin/env python3
"""
Example of using custom frame processors with SLDP Grabber
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sldp_grabber import SLDPGrabber

class FrameProcessor:
    def __init__(self):
        self.video_count = 0
        self.audio_count = 0
    
    def on_video_frame(self, data, meta):
        self.video_count += 1
        if self.video_count % 30 == 0:  # Print every 30 frames
            print(f"\nVideo frame {self.video_count}: {len(data)} bytes, "
                  f"keyframe: {meta.get('keyframe', False)}")
    
    def on_audio_frame(self, data, meta):
        self.audio_count += 1
        if self.audio_count % 100 == 0:  # Print every 100 frames
            print(f"\nAudio frame {self.audio_count}: {len(data)} bytes")

def main():
    processor = FrameProcessor()
    
    grabber = SLDPGrabber(
        url="wss://.../stream",  # Replace with actual URL
        out_dir="processed_recordings",
        duration=10,  # 10 seconds
        on_video_frame=processor.on_video_frame,
        on_audio_frame=processor.on_audio_frame
    )
    
    print("Starting recording with custom processor...")
    grabber.run()
    print(f"Processed {processor.video_count} video frames and {processor.audio_count} audio frames")

if __name__ == "__main__":
    main()