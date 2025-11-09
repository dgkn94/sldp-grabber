"""
sldp-grabber package.

Provides:
- SLDPGrabber: main grabbing/recording class
- StreamInfo: stream metadata container
"""

from .grabber import SLDPGrabber, StreamInfo
import logging

__all__ = ["SLDPGrabber", "StreamInfo"]

# Package-wide logger (used by helpers/utilities)
log = logging.getLogger("sldp_grabber")

# Keep version in one place for setup.py / pyproject.toml
__version__ = "1.0.0"
