# sldp_grabber/cli.py
#!/usr/bin/env python3
"""
Command-line interface for SLDP Grabber
"""

import argparse
import logging
import shutil
import sys
from pathlib import Path

from .grabber import SLDPGrabber
from .utils import load_headers_from_file, parse_headers, parse_pipe_command
from . import __version__

log = logging.getLogger("sldp_grabber")


def main():
    parser = argparse.ArgumentParser(
        description="Grab streams from SLDP WebSocket servers"
    )

    core = parser.add_argument_group("Core")
    core.add_argument("-u", "--url", required=True, help="WebSocket URL (wss://...)")

    output = parser.add_argument_group("Output Options")
    output.add_argument("--out-dir", default="recordings", help="Where to save files")
    output.add_argument(
        "-d",
        "--duration",
        type=int,
        default=0,
        help="How long to record in seconds (0 = until stopped)",
    )
    output.add_argument(
        "-o",
        "--output",
        help="Base name for output file (without extension)",
    )
    output.add_argument(
        "--no-mux",
        "--no-mp4",
        dest="no_mux",
        action="store_true",
        help="Keep raw streams, don't create MP4 (applies to single file and segments).",
    )
    output.add_argument(
        "--segment-duration",
        type=int,
        default=0,
        help="Split into chunks of this many seconds (0 = single file)",
    )
    output.add_argument(
        "--keep-raw",
        action="store_true",
        help="Keep raw .h264/.aac/.opus files after muxing",
    )

    connect = parser.add_argument_group("Connection Options")
    connect.add_argument(
        "-H",
        "--header",
        action="append",
        help='Extra header (e.g., --header "Cookie: token=abc")',
    )
    connect.add_argument("--header-file", help="File with extra headers")
    connect.add_argument(
        "--origin",
        default="https://softvelum.com",
        help="Origin header",
    )
    connect.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="How many times to retry connection",
    )
    connect.add_argument(
        "--retry-delay",
        type=float,
        default=2.0,
        help="Seconds to wait between retries",
    )

    parser.add_argument("--stream", help="Specific stream name to grab")
    parser.add_argument(
        "-L",
        "--list-streams",
        action="store_true",
        help="Just list available streams and exit",
    )

    advanced = parser.add_argument_group("Advanced Options")
    advanced.add_argument(
        "--pipe-h264-cmd",
        help=(
            "Command to pipe H.264 video to via stdin. "
            'Example: --pipe-h264-cmd "ffplay -fflags nobuffer -flags low_delay -f h264 -i -"'
        ),
    )
    advanced.add_argument(
        "--pipe-aac-cmd",
        help=(
            "Command to pipe AAC/Opus audio to via stdin. "
            'Example: --pipe-aac-cmd "ffplay -f aac -i -"'
        ),
    )
    advanced.add_argument(
        "--strict",
        action="store_true",
        help="Exit on unsupported codecs",
    )
    advanced.add_argument(
        "--debug-ts",
        action="store_true",
        help="Save frame timestamps to CSV for debugging",
    )
    advanced.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase log verbosity (-v for DEBUG, -vv for very noisy in future)",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    args = parser.parse_args()

    # Configure logging once for the CLI
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
        stream=sys.stdout,
    )

    if args.verbose >= 1:
        logging.getLogger().setLevel(logging.DEBUG)

    headers = parse_headers(args.header)
    file_headers = load_headers_from_file(args.header_file)
    headers.update(file_headers)

    create_container = (not args.no_mux) and (not args.list_streams)

    if create_container and not shutil.which("ffmpeg"):
        log.error(
            "ffmpeg is needed for MP4 creation but wasn't found. "
            "Install ffmpeg or use --no-mux to skip container creation."
        )
        sys.exit(1)

    pipe_h264_cmd = parse_pipe_command(args.pipe_h264_cmd)
    pipe_aac_cmd = parse_pipe_command(args.pipe_aac_cmd)

    grabber = SLDPGrabber(
        url=args.url,
        stream_name=args.stream,
        out_dir=args.out_dir,
        duration=args.duration,
        origin=args.origin,
        headers=headers,
        list_only=args.list_streams,
        max_retries=args.max_retries,
        retry_delay=args.retry_delay,
        segment_duration=args.segment_duration,
        pipe_h264_cmd=pipe_h264_cmd,
        pipe_aac_cmd=pipe_aac_cmd,
        debug_ts=args.debug_ts,
        strict=args.strict,
        keep_raw=args.keep_raw,
    )

    grabber.run(create_mp4=create_container, mp4_base=args.output)


if __name__ == "__main__":
    main()
