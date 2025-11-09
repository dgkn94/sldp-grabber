"""
Utility helpers for sldp-grabber.
"""

import logging
import shlex
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("sldp_grabber")


def load_headers_from_file(path: Optional[str]) -> Dict[str, str]:
    """Load HTTP headers from a simple 'Key: Value' text file."""
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        log.error("Header file %s not found", path)
        return {}
    headers: Dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        headers[k].strip()
        headers[k.strip()] = v.strip()
    return headers


def parse_headers(header_args: Optional[list]) -> Dict[str, str]:
    """Parse --header 'Key: Value' occurrences from CLI."""
    headers: Dict[str, str] = {}
    if not header_args:
        return headers
    for header in header_args:
        if ":" not in header:
            log.warning("Skipping invalid header: %s", header)
            continue
        k, v = header.split(":", 1)
        headers[k.strip()] = v.strip()
    return headers


def parse_pipe_command(cmd_str: Optional[str]) -> Optional[List[str]]:
    """Parse pipe command string into argv, handling quotes."""
    if not cmd_str:
        return None
    try:
        return shlex.split(cmd_str)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log.error("Failed to parse pipe command '%s': %s", cmd_str, exc)
        return None
