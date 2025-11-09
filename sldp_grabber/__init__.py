"""
sldp-grabber package.

Exports:
- SLDPGrabber: main grabbing/recording class
- StreamInfo: stream metadata container
"""

import logging

from .grabber import SLDPGrabber, StreamInfo

__all__ = ["SLDPGrabber", "StreamInfo"]

log = logging.getLogger("sldp_grabber")

__version__ = "1.0.0"
