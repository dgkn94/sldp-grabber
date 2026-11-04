"""
Core grabbing / recording logic for sldp-grabber.

This module is intentionally large and pragmatic; some pylint checks are disabled.
"""

# pylint: disable=too-many-lines,too-many-instance-attributes,too-many-public-methods,
# pylint: disable=too-few-public-methods,too-many-arguments,too-many-locals,
# pylint: disable=too-many-branches,too-many-statements,too-many-return-statements,
# pylint: disable=broad-exception-caught,attribute-defined-outside-init

import csv
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import shutil
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Dict, Any, List

try:
    import websocket
except ImportError:
    print("ERROR: You need to install the websocket-client package")
    print("Run: pip install websocket-client")
    sys.exit(1)


log = logging.getLogger("sldp_grabber")

# Don't log sensitive headers
SENSITIVE_HEADERS = ("cookie", "set-cookie", "authorization", "proxy-authorization")


def hide_sensitive_headers(key: str, value: str) -> str:
    """Hide sensitive headers in logs"""
    key_lower = key.lower().strip()
    if key_lower in SENSITIVE_HEADERS:
        return "***"
    if key_lower.startswith("authorization") or key_lower.startswith("proxy-authorization"):
        return "***"
    return value


class StreamInfo:
    """Keep track of what we know about a stream (video or audio)."""

    def __init__(self, is_video: bool):
        self.is_video = is_video
        self.stream_name: Optional[str] = None
        self.codec: Optional[str] = None
        self.timescale: int = 1000  # usually milliseconds

        # Video specific stuff
        self.width: int = 0
        self.height: int = 0

        # Codec configuration data
        self.extradata: Optional[bytes] = None  # avcC for h264, ASC for AAC, etc
        self.nalu_length_size: int = 4  # for h264
        self.sps_pps_data: Optional[bytes] = None  # h264 parameter sets
        self.sps_pps_written: bool = False  # track if we've written headers

        # Some stats for debugging
        self.frame_count: int = 0
        self.first_ts: Optional[int] = None
        self.last_ts: Optional[int] = None


class SLDPGrabber:
    """
    Main class that does the actual work of grabbing streams.
    You can use this as a library or just run it directly.
    """

    # These come from the SLDP protocol framing
    AAC_HEADER = 0
    AAC_FRAME = 1
    AVC_HEADER = 2
    AVC_KEYFRAME = 3
    AVC_FRAME = 4

    def __init__(
        self,
        url: str,
        stream_name: Optional[str] = None,
        out_dir: str = "recordings",
        duration: int = 0,
        origin: str = "https://softvelum.com",
        headers: Optional[Dict[str, str]] = None,
        list_only: bool = False,
        max_retries: int = 3,
        retry_delay: float = 2.0,
        segment_duration: int = 0,
        pipe_h264_cmd: Optional[List[str]] = None,
        pipe_aac_cmd: Optional[List[str]] = None,
        debug_ts: bool = False,
        strict: bool = False,
        keep_raw: bool = False,
        on_video_frame: Optional[Callable[[bytes, Dict[str, Any]], None]] = None,
        on_audio_frame: Optional[Callable[[bytes, Dict[str, Any]], None]] = None,
    ):
        self.url = url
        self.stream_name = stream_name
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        if duration < 0 or segment_duration < 0 or retry_delay < 0 or max_retries < 1:
            raise ValueError("Durations and retry delay must be nonnegative; max_retries must be at least 1")

        self.pipe_video_started = False
        self.duration = duration  # 0 means run until stopped
        self.list_only = list_only
        self.keep_raw = keep_raw
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.segment_duration = segment_duration  # split into chunks if > 0
        self.segment_create_mp4 = False  # will be set later

        self.strict = strict  # be picky about codecs

        # Callbacks for custom processing
        self.on_video_frame = on_video_frame
        self.on_audio_frame = on_audio_frame

        # WebSocket stuff
        self.ws: Optional[websocket.WebSocketApp] = None
        self.ws_connected: bool = False
        self.should_run: bool = True
        self._callback_error = None
        self._waiting_for_keyframe = True
        self._mux_executor = None
        self._mux_jobs = []

        # Track the streams we find
        self.video = StreamInfo(is_video=True)
        self.audio = StreamInfo(is_video=False)

        # These seem to be standard IDs in SLDP
        self.video_id = 0
        self.audio_id = 1

        # For segment management
        self.segment_index: int = 0
        self.segment_start_wall: Optional[float] = None

        # File handles
        self.h264_file: Optional[Path] = None
        self.aud_file: Optional[Path] = None
        self.video_writer = None
        self.audio_writer = None
        self._created_files = set()

        # Piping to external commands (argv lists, no shell=True)
        self.pipe_h264_cmd = pipe_h264_cmd
        self.pipe_aac_cmd = pipe_aac_cmd
        self.h264_pipe: Optional[subprocess.Popen] = None
        self.aac_pipe: Optional[subprocess.Popen] = None

        # Audio configuration
        self.aac_rate = 44100  # default
        self.aac_channels = 2  # default

        # Figure out what kind of audio we are dealing with
        self.audio_is_aac = False
        self.audio_is_opus = False
        self.audio_ext = "aac"  # will change if we detect opus

        # Count frames for current file/segment
        self.video_frames = 0
        self.audio_frames = 0

        # Debug timing info
        self.debug_ts = debug_ts
        self.ts_rows: List[List[Any]] = []

        # Setup WebSocket headers
        self.ws_headers = {
            "Origin": origin,
            "Sec-WebSocket-Protocol": "sldp.softvelum.com",
            "User-Agent": "Mozilla/5.0 (compatible; SLDP-Grabber/1.0)",
        }
        if headers:
            for key, value in headers.items():
                for existing in list(self.ws_headers):
                    if existing.lower() == key.lower():
                        del self.ws_headers[existing]
                self.ws_headers[key] = value

    # ---------- WebSocket callbacks ----------

    def ws_on_open(self, ws):
        self.ws_connected = True
        if not self.list_only:
            log.info("Connected to server")
            
    def ws_on_error(self, ws, error):
        log.error("WebSocket error: %s", error)

    def ws_on_close(self, ws, code, message):
        log.info("Connection closed (code: %s)", code)
        self.ws_connected = False
        if self.list_only:
            self.should_run = False

    def ws_on_message(self, ws, message):
        if not self.should_run:
            return

        try:
            if isinstance(message, str):
                self.handle_text_message(message)
            else:
                self.handle_binary_message(message)
        except Exception as exc:
            self._callback_error = exc
            self.should_run = False
            ws.close()

    # ---------- Text messages ----------

    def handle_text_message(self, text: str):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.warning("Got weird JSON from server, skipping")
            return

        if not isinstance(data, dict) or data.get("command") != "status":
            return

        if self.list_only:
            self.print_stream_list(data)
            self.should_run = False
            if self.ws:
                try:
                    self.ws.close()
                except Exception:
                    pass
            return

        self.parse_stream_info(data)
        self.request_streams()

    def print_stream_list(self, data: Dict[str, Any]):
        info = data.get("info", [])
        if not info:
            log.info("No streams found in server response")
            return

        log.info("Found these streams:")
        for stream_info in info:
            name = stream_info.get("stream", "unnamed")
            details = stream_info.get("stream_info", {})
            log.info("  %s: %s", name, details)

    def parse_stream_info(self, data: dict):
        self.video.stream_name = None
        self.audio.stream_name = None
        self.audio_is_aac = False
        self.audio_is_opus = False
        streams = data.get("info", [])
        if not streams:
            log.warning("No stream info in server message")
            return

        # Filter to specific stream if requested
        if self.stream_name:
            filtered = [s for s in streams if s.get("stream") == self.stream_name]
            if not filtered:
                msg = f"Stream '{self.stream_name}' not found"
                if self.strict:
                    raise RuntimeError(msg)
                log.error(msg)
                return
        else:
            # Keep audio and video on the same rendition.
            self.stream_name = streams[0].get("stream")
            filtered = [s for s in streams if s.get("stream") == self.stream_name]

        for stream in filtered:
            info = stream.get("stream_info", {}) or {}
            name = stream.get("stream", "")

            # Video
            if "vcodec" in info:
                self.video.stream_name = name
                self.video.codec = info.get("vcodec")
                self.video.timescale = int(info.get("vtimescale", 1000))

                codec_lower = (self.video.codec or "").lower()
                if codec_lower.startswith("avc1.") or "h264" in codec_lower or "avc" in codec_lower:
                    res = info.get("resolution", "")
                    if "x" in res:
                        try:
                            w, h = res.split("x")
                            self.video.width, self.video.height = int(w), int(h)
                        except (ValueError, TypeError):
                            pass

                    log.info(
                        "Video: %s %dx%d (timescale: %d)",
                        self.video.codec,
                        self.video.width,
                        self.video.height,
                        self.video.timescale,
                    )
                else:
                    if self.strict:
                        raise RuntimeError(f"Strict mode: unsupported video codec {self.video.codec}")
                    log.warning("Video codec %s not supported - skipping video", self.video.codec)
                    self.video.stream_name = None
                    self.video.codec = None

            # Audio
            if "acodec" in info:
                self.audio.stream_name = name
                self.audio.codec = info.get("acodec")
                self.audio.timescale = int(info.get("atimescale", 1000))

                codec_lower = (self.audio.codec or "").lower()
                if codec_lower.startswith("mp4a."):
                    self.audio_is_aac = True
                    self.audio_is_opus = False
                    self.audio_ext = "aac"
                    log.info("Audio: %s (AAC, timescale: %d)", self.audio.codec, self.audio.timescale)
                elif "opus" in codec_lower:
                    self.audio_is_aac = False
                    self.audio_is_opus = True
                    self.audio_ext = "opus"
                    log.info(
                        "Audio: %s (Opus, timescale: %d) - saving raw packets",
                        self.audio.codec,
                        self.audio.timescale,
                    )
                else:
                    if self.strict:
                        raise RuntimeError(f"Strict mode: unsupported audio codec {self.audio.codec}")
                    log.warning("Audio codec %s not supported - skipping audio", self.audio.codec)
                    self.audio.stream_name = None
                    self.audio_is_aac = False
                    self.audio_is_opus = False

        # Auto-select
        if not self.stream_name:
            if self.video.stream_name:
                self.stream_name = self.video.stream_name
            elif self.audio.stream_name:
                self.stream_name = self.audio.stream_name

    def request_streams(self):
        streams_to_play = []

        if self.video.stream_name:
            streams_to_play.append(
                {"sn": self.video_id, "stream": self.video.stream_name, "type": "video"}
            )
        if self.audio.stream_name:
            streams_to_play.append(
                {"sn": self.audio_id, "stream": self.audio.stream_name, "type": "audio"}
            )

        if not streams_to_play:
            log.error("No streams to play - check stream names/codecs")
            return

        if not self.ws:
            log.error("WebSocket not ready, can't send Play command")
            return

        try:
            self.ws.send(json.dumps({"command": "Play", "streams": streams_to_play}))
            if self.segment_duration > 0:
                if self.segment_index == 0:
                    self.open_segment_outputs()
            else:
                # Single-file mode: open writers
                if self.video.stream_name and not self.video_writer:
                    if not self.h264_file:
                        self.h264_file = self._available_path(self.out_dir / "video.h264")
                    self._open_video_writer(self.h264_file)

                if self.audio.stream_name and not self.audio_writer:
                    if not self.aud_file:
                        self.aud_file = self._available_path(self.out_dir / f"audio.{self.audio_ext}")
                    self._open_audio_writer(self.aud_file)

                # Start pipes in non-segment mode too
                if self.h264_pipe is None:
                    self.h264_pipe = self._start_pipe(self.pipe_h264_cmd, "H.264")
                if self.aac_pipe is None:
                    self.aac_pipe = self._start_pipe(self.pipe_aac_cmd, "audio")

        except Exception as e:
            log.error("Failed to send Play command: %s", e)
            raise

    # ---------- Pipes and file helpers ----------   

    @staticmethod
    def _available_path(path: Path) -> Path:
        candidate = path
        index = 1
        while candidate.exists():
            candidate = path.with_name(f"{path.stem}({index}){path.suffix}")
            index += 1
        return candidate

    def _start_pipe(self, cmd: List[str], label: str) -> Optional[subprocess.Popen]:
        """Safely start a pipe command with error handling."""
        if not cmd:
            return None
        try:
            return subprocess.Popen(cmd, stdin=subprocess.PIPE)
        except Exception as e:
            log.error("Failed to start %s pipe '%s': %s", label, " ".join(cmd), e)
            return None

    def _stop_pipe(self, proc: Optional[subprocess.Popen], label: str):
        if not proc:
            return

        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:
            pass

        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("%s pipe didn't exit cleanly, killing it", label)
            proc.kill()
            proc.wait()
        except Exception as e:
            log.warning("Error waiting for %s pipe: %s", label, e)
        else:
            if proc.returncode not in (0, None):
                log.warning("%s pipe exited with code %s", label, proc.returncode)

    def _open_video_writer(self, path: Path):
        self.close_video_writer()
        try:
            self.video_writer = open(path, "xb")
            self._created_files.add(path)
        except OSError as e:
            log.error("Failed to open video file %s: %s", path, e)
            self.video_writer = None
            raise

    def _open_audio_writer(self, path: Path):
        self.close_audio_writer()
        try:
            self.audio_writer = open(path, "xb")
            self._created_files.add(path)
        except OSError as e:
            log.error("Failed to open audio file %s: %s", path, e)
            self.audio_writer = None
            raise

    def close_video_writer(self):
        if self.video_writer:
            try:
                self.video_writer.close()
            except Exception as e:
                log.warning("Error closing video file: %s", e)
            self.video_writer = None

    def close_audio_writer(self):
        if self.audio_writer:
            try:
                self.audio_writer.close()
            except Exception as e:
                log.warning("Error closing audio file: %s", e)
            self.audio_writer = None

    def open_segment_outputs(self):
        if self.segment_index == 0:
            self.segment_index = 1
        else:
            self.segment_index += 1

        while any((self.out_dir / f"segment_{self.segment_index:04d}.{ext}").exists()
                  for ext in ("h264", "aac", "opus", "mp4", "mkv")):
            self.segment_index += 1

        self.segment_start_wall = time.monotonic()

        base = f"segment_{self.segment_index:04d}"
        self.h264_file = self.out_dir / f"{base}.h264"
        self.aud_file = self.out_dir / f"{base}.{self.audio_ext}"

        if self.video.stream_name:
            self._open_video_writer(self.h264_file)
        if self.audio.stream_name:
            self._open_audio_writer(self.aud_file)

        self.video_frames = 0
        self.audio_frames = 0
        self.video.sps_pps_written = False
        self.video.frame_count = 0
        self.video.first_ts = None
        self.video.last_ts = None
        self._waiting_for_keyframe = True
        self.pipe_video_started = False

        # Restart pipes using argv list
        self.h264_pipe = self._start_pipe(self.pipe_h264_cmd, "H.264")
        self.aac_pipe = self._start_pipe(self.pipe_aac_cmd, "audio")

    def check_segment_rollover(self, is_keyframe: bool = False):
        if self.segment_duration <= 0 or self.segment_start_wall is None:
            return

        if self.video.stream_name and not is_keyframe:
            return
        if time.monotonic() - self.segment_start_wall < self.segment_duration:
            return

        self.finish_current_segment(create_mp4=self.segment_create_mp4)
        self.open_segment_outputs()

    def finish_current_segment(self, create_mp4: bool = True):
        self._stop_pipe(self.h264_pipe, "H.264")
        self.h264_pipe = None
        self._stop_pipe(self.aac_pipe, "audio")
        self.aac_pipe = None

        self.close_video_writer()
        self.close_audio_writer()

        # Mux closed files on one worker; receiving the next segment can continue.
        if create_mp4:
            if self._mux_executor is None:
                self._mux_executor = ThreadPoolExecutor(max_workers=1)
            segment = copy.copy(self)
            segment.video = copy.copy(self.video)
            self._mux_jobs.append(self._mux_executor.submit(segment._finish_segment_outputs, True))
        else:
            self._finish_segment_outputs(False)

    def _finish_segment_outputs(self, create_mp4: bool):

        if not self.h264_file and not self.aud_file:
            return

        has_video = (
            self.h264_file
            and self.h264_file in self._created_files
            and self.h264_file.exists()
            and self.h264_file.stat().st_size > 0
        )
        has_audio = (
            self.aud_file
            and self.aud_file in self._created_files
            and self.aud_file.exists()
            and self.aud_file.stat().st_size > 0
        )

        if not has_video and not has_audio:
            if self.h264_file in self._created_files and self.h264_file.exists():
                self.h264_file.unlink()
            if self.aud_file in self._created_files and self.aud_file.exists():
                self.aud_file.unlink()
            return

        if create_mp4:
            base = self.out_dir / f"segment_{self.segment_index:04d}"
            out = self.mux_to_container(
                self.h264_file if has_video else None,
                self.aud_file if has_audio else None,
                base,
            )
            if out is not None and not self.keep_raw:
                if has_video and self.h264_file and self.h264_file.exists():
                    self.h264_file.unlink()
                if has_audio and self.aud_file and self.aud_file.exists():
                    if self.audio_is_aac:
                        self.aud_file.unlink()
                    else:
                        log.info(
                            "Keeping %s (can't mux this audio format to MP4)",
                            self.aud_file.name,
                        )

    # ---------- Binary + frame processing ----------

    def handle_binary_message(self, data: bytes):
        if len(data) < 2:
            return

        stream_id = data[0]
        frame_type = data[1]
        pos = 2

        timestamp = None
        if frame_type not in (self.AAC_HEADER, self.AVC_HEADER):
            if len(data) >= pos + 8:
                timestamp = struct.unpack(">Q", data[pos:pos + 8])[0]
                pos += 8
            else:
                log.debug("Frame missing timestamp (len=%d)", len(data))
                return

        cto = None
        if frame_type in (self.AVC_KEYFRAME, self.AVC_FRAME):
            if len(data) >= pos + 4:
                cto = struct.unpack(">i", data[pos:pos + 4])[0]
                pos += 4
            else:
                log.debug("Video frame missing CTO")
                return

        payload = data[pos:]

        if stream_id == self.video_id:
            self.process_video_frame(frame_type, timestamp, cto, payload)
        elif stream_id == self.audio_id:
            self.process_audio_frame(frame_type, timestamp, payload)

    def process_video_frame(self, frame_type: int, timestamp, cto, data: bytes):
        if frame_type == self.AVC_HEADER:
            if not data or len(data) < 7:
                return
            try:
                nalu_size = (data[4] & 0x03) + 1
                if nalu_size not in (1, 2, 4):
                    return
                sps_pps = self.extract_sps_pps(data)
                if not sps_pps:
                    return
                self.video.extradata = data
                self.video.nalu_length_size = nalu_size
                self.video.sps_pps_data = sps_pps
                self.video.sps_pps_written = False
                log.info("Got video configuration (%d bytes)", len(data))
            except Exception as e:
                log.warning("Failed to parse video config: %s", e)
            return

        if frame_type not in (self.AVC_KEYFRAME, self.AVC_FRAME):
            return
        if not self.video.extradata or not data:
            return
        if not self.video_writer and not self.h264_pipe:
            return

        is_keyframe = (frame_type == self.AVC_KEYFRAME)
        if self._waiting_for_keyframe and not is_keyframe:
            return
        annexb = self.convert_to_annexb(data, self.video.nalu_length_size)
        if not annexb:
            return

        self.check_segment_rollover(is_keyframe=is_keyframe)
        self._waiting_for_keyframe = False

        if self.video.sps_pps_data and (is_keyframe or not self.video.sps_pps_written):
            if self.video_writer:
                self.video_writer.write(self.video.sps_pps_data)
            self.video.sps_pps_written = True

        if timestamp is not None:
            if self.video.first_ts is None:
                self.video.first_ts = timestamp
            self.video.last_ts = timestamp
            self.video.frame_count += 1

        if self.video_writer:
            self.video_writer.write(annexb)
        if self.h264_pipe and self.h264_pipe.stdin:
            try:
                if is_keyframe and not self.pipe_video_started:
                    if self.video.sps_pps_data:
                        self.h264_pipe.stdin.write(self.video.sps_pps_data)
                    self.h264_pipe.stdin.write(annexb)
                    self.pipe_video_started = True
                elif self.pipe_video_started:
                    self.h264_pipe.stdin.write(annexb)
            except OSError as e:
                # ffplay closed / pipe broken -> stop piping AND stop the program
                log.info("H.264 pipe closed (%s) - stopping live piping and exiting", e)
                self._stop_pipe(self.h264_pipe, "H.264")
                self.h264_pipe = None
                self.should_run = False
                if self.ws:
                    try:
                        self.ws.close()
                    except Exception:
                        pass
                return  # bail out of this frame

        if self.on_video_frame:
            meta = {"timestamp": timestamp, "cto": cto, "keyframe": is_keyframe}
            try:
                frame = (self.video.sps_pps_data or b"") + annexb if is_keyframe else annexb
                self.on_video_frame(frame, meta)
            except Exception:
                log.exception("Video frame callback failed")

        self.video_frames += 1

        if self.debug_ts and timestamp is not None:
            self.ts_rows.append(
                ["video", frame_type, timestamp, cto if cto is not None else ""]
            )

    def process_audio_frame(self, frame_type: int, timestamp, data: bytes):
        # AAC
        if self.audio_is_aac:
            if frame_type == self.AAC_HEADER:
                if not data:
                    return
                self.audio.extradata = data
                rate, channels = self.parse_audio_config(data)
                self.aac_rate = rate
                self.aac_channels = channels
                log.info("Got AAC config: %d Hz, %d channels", rate, channels)
                return

            if frame_type != self.AAC_FRAME or not data or not self.audio.extradata:
                return
            if not self.audio_writer and not self.aac_pipe:
                return

            if self.video.stream_name and self._waiting_for_keyframe:
                return
            self.check_segment_rollover()

            adts = self.make_adts_header(len(data), self.aac_rate, self.aac_channels)
            frame = adts + data

            if self.audio_writer:
                self.audio_writer.write(frame)
            if self.aac_pipe and self.aac_pipe.stdin:
                try:
                    self.aac_pipe.stdin.write(frame)
                except OSError as e:
                    log.info("Audio pipe closed (%s) - stopping live piping", e)
                    self._stop_pipe(self.aac_pipe, "audio")
                    self.aac_pipe = None
            if self.on_audio_frame:
                meta = {"timestamp": timestamp}
                try:
                    self.on_audio_frame(frame, meta)
                except Exception:
                    log.exception("Audio frame callback failed")

            self.audio_frames += 1

            if self.debug_ts and timestamp is not None:
                self.ts_rows.append(["audio", frame_type, timestamp, ""])

            return

        # Opus (raw packets)
        if self.audio_is_opus:
            if frame_type == self.AAC_HEADER:
                self.audio.extradata = data or b""
                if self.audio.extradata:
                    log.info("Got Opus config (%d bytes)", len(self.audio.extradata))
                return

            if frame_type != self.AAC_FRAME or not data:
                return
            if not self.audio_writer and not self.aac_pipe:
                return

            if self.video.stream_name and self._waiting_for_keyframe:
                return
            self.check_segment_rollover()

            if self.audio_writer:
                self.audio_writer.write(data)
            if self.aac_pipe and self.aac_pipe.stdin:
                try:
                    self.aac_pipe.stdin.write(data)
                except OSError as e:
                    log.info("Audio pipe closed (%s) - stopping live piping", e)
                    self._stop_pipe(self.aac_pipe, "audio")
                    self.aac_pipe = None
            if self.on_audio_frame:
                meta = {"timestamp": timestamp}
                try:
                    self.on_audio_frame(data, meta)
                except Exception:
                    log.exception("Audio frame callback failed")

            self.audio_frames += 1

            if self.debug_ts and timestamp is not None:
                self.ts_rows.append(["audio-opus", frame_type, timestamp, ""])

            return

    # ---------- Static helpers ----------

    @staticmethod
    def extract_sps_pps(avcc_data: bytes) -> bytes:
        if not avcc_data or len(avcc_data) < 7:
            return b""

        try:
            pos = 5
            if pos >= len(avcc_data):
                return b""

            out = bytearray()

            # SPS
            sps_count = avcc_data[pos] & 0x1F
            pos += 1
            if not sps_count:
                return b""
            for _ in range(sps_count):
                if pos + 2 > len(avcc_data):
                    return b""
                sps_len = (avcc_data[pos] << 8) | avcc_data[pos + 1]
                pos += 2
                if sps_len <= 0 or pos + sps_len > len(avcc_data):
                    return b""
                out.extend(b"\x00\x00\x00\x01")
                out.extend(avcc_data[pos:pos + sps_len])
                pos += sps_len

            if pos >= len(avcc_data):
                return b""

            # PPS
            pps_count = avcc_data[pos]
            pos += 1
            if not pps_count:
                return b""
            for _ in range(pps_count):
                if pos + 2 > len(avcc_data):
                    return b""
                pps_len = (avcc_data[pos] << 8) | avcc_data[pos + 1]
                pos += 2
                if pps_len <= 0 or pos + pps_len > len(avcc_data):
                    return b""
                out.extend(b"\x00\x00\x00\x01")
                out.extend(avcc_data[pos:pos + pps_len])
                pos += pps_len

            return bytes(out)
        except Exception:
            return b""

    @staticmethod
    def convert_to_annexb(data: bytes, nalu_size: int) -> bytes:
        if not data or nalu_size not in (1, 2, 4):
            return b""

        out = bytearray()
        pos = 0
        total_len = len(data)

        while pos + nalu_size <= total_len:
            nalu_len = 0
            for i in range(nalu_size):
                nalu_len = (nalu_len << 8) | data[pos + i]
            pos += nalu_size

            if nalu_len <= 0 or pos + nalu_len > total_len:
                return b""

            out.extend(b"\x00\x00\x00\x01")
            out.extend(data[pos:pos + nalu_len])
            pos += nalu_len

        return bytes(out) if pos == total_len else b""

    @staticmethod
    def parse_audio_config(asc_data: bytes):
        if len(asc_data) < 2:
            return 44100, 2

        sample_rates = [
            96000, 88200, 64000, 48000, 44100, 32000,
            24000, 22050, 16000, 12000, 11025, 8000, 7350,
        ]

        sr_index = ((asc_data[0] & 0x07) << 1) | ((asc_data[1] >> 7) & 0x01)

        if sr_index >= len(sample_rates) and sr_index != 0x0F:
            log.warning("Weird sample rate index %d, using 44100", sr_index)

        if sr_index == 0x0F:
            rate = 44100
            channels = 2
        else:
            rate = sample_rates[sr_index] if sr_index < len(sample_rates) else 44100
            channels = (asc_data[1] >> 3) & 0x0F

        if channels <= 0:
            log.warning("Channel config 0, assuming stereo")
            channels = 2
        return rate, channels

    @staticmethod
    def make_adts_header(data_len: int, sample_rate: int, channels: int) -> bytes:
        sample_rates = [
            96000, 88200, 64000, 48000, 44100, 32000,
            24000, 22050, 16000, 12000, 11025, 8000, 7350,
        ]
        try:
            sr_index = sample_rates.index(sample_rate)
        except ValueError:
            sr_index = 4  # 44100

        profile = 1  # AAC LC
        frame_length = data_len + 7

        header = bytearray(7)
        header[0] = 0xFF
        header[1] = 0xF1
        header[2] = (
            ((profile & 0x03) << 6)
            | ((sr_index & 0x0F) << 2)
            | ((channels >> 2) & 0x01)
        )
        header[3] = (
            ((channels & 0x03) << 6)
            | ((frame_length >> 11) & 0x03)
        )
        header[4] = (frame_length >> 3) & 0xFF
        header[5] = ((frame_length & 0x07) << 5) | 0x1F
        header[6] = 0xFC

        return bytes(header)

    @staticmethod
    def estimate_fps(stream: StreamInfo) -> Optional[float]:
        fc = stream.frame_count
        t0 = stream.first_ts
        t1 = stream.last_ts
        ts = stream.timescale

        if fc < 2 or t0 is None or t1 is None or not ts:
            return None

        delta = t1 - t0
        if delta <= 0:
            return None

        duration = float(delta) / float(ts)
        if duration <= 0:
            return None

        fps = (fc - 1) / duration
        if fps < 1 or fps > 120:
            return None

        return fps

    def make_output_mp4_path(self, base_name: Optional[str]) -> Path:
        if base_name:
            base = Path(base_name).stem
        else:
            base = self.stream_name.split("/")[-1] if self.stream_name else "recording"

        safe_name = "".join(
            c if (c.isalnum() or c in "-_.") else "_"
            for c in base
        ) or "recording"

        candidate = self.out_dir / f"{safe_name}.mp4"
        if not candidate.exists() and not candidate.with_suffix(".mkv").exists():
            return candidate

        i = 1
        while True:
            candidate = self.out_dir / f"{safe_name}({i}).mp4"
            if not candidate.exists() and not candidate.with_suffix(".mkv").exists():
                return candidate
            i += 1

    def mux_to_container(self, h264_path, audio_path, output_base) -> Optional[Path]:
        if not shutil.which("ffmpeg"):
            log.error("ffmpeg not found - can't create container")
            return None

        video_input = (
            str(h264_path)
            if h264_path and h264_path.exists() and h264_path.stat().st_size > 0
            else None
        )
        audio_input = (
            str(audio_path)
            if audio_path and audio_path.exists() and audio_path.stat().st_size > 0
            else None
        )

        if not video_input and not audio_input:
            log.error("No media to mux")
            return None

        if self.audio_is_aac and audio_input:
            out_path = Path(str(output_base) + ".mp4")
        elif self.audio_is_opus:
            if audio_input:
                log.warning(
                    "Opus sidecar %s kept; not muxing raw Opus directly here",
                    audio_input,
                )
            if video_input:
                out_path = Path(str(output_base) + ".mkv")
                audio_input = None
            else:
                log.info("Audio-only Opus - no container created")
                return None
        else:
            out_path = Path(str(output_base) + ".mp4")

        cmd = ["ffmpeg", "-n", "-hide_banner", "-nostdin"]

        fps = self.estimate_fps(self.video) if video_input else None
        if video_input:
            if fps:
                cmd += ["-r", f"{fps:.6f}"]
            cmd += ["-fflags", "+genpts", "-i", video_input]
        if audio_input:
            cmd += ["-i", audio_input]

        cmd += ["-c", "copy", str(out_path)]

        log.info("Running ffmpeg: %s", " ".join(cmd))
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                log.error("ffmpeg failed for %s: %s", out_path.name, r.stderr.strip())
                return None
        except Exception as e:
            log.error("Error running ffmpeg for %s: %s", out_path.name, e)
            return None

        log.info("Created %s", out_path)
        return out_path

    def _print_progress(self, start_time: float, total_duration: Optional[int]):
        """Print a lightweight progress indicator."""
        if not start_time:
            return

        now = time.monotonic()
        last = getattr(self, "_last_progress", 0.0)
        if now - last < 1.0:
            return
        self._last_progress = now

        elapsed = int(now - start_time)

        if total_duration and total_duration > 0:
            safe_elapsed = min(elapsed, total_duration)
            remaining = max(0, total_duration - safe_elapsed)
            pct = min(100, int(safe_elapsed * 100 / total_duration))
            msg = f"Recording: {safe_elapsed}s elapsed, {remaining}s left ({pct}%)"
        else:
            msg = f"Recording: {elapsed}s elapsed"

        if sys.stderr.isatty():
            print(f"\r{msg} ", end="", file=sys.stderr, flush=True)
        else:
            log.info(msg)

    # ---------- Finalization & run loop ----------

    def _close_websocket(self):
        if self.ws is None:
            return
        self.ws.keep_running = False
        sock = self.ws.sock
        if sock is not None:
            try:
                # Wake the receive thread before closing its socket on another thread.
                sock.abort()
            except OSError:
                pass
        self.ws.close(timeout=0)

    def finalize(self, create_mp4: bool, mp4_base: Optional[str]):
        if self.list_only:
            return

        self._stop_pipe(self.h264_pipe, "H.264")
        self.h264_pipe = None
        self._stop_pipe(self.aac_pipe, "audio")
        self.aac_pipe = None

        if self.segment_duration > 0:
            self.finish_current_segment(create_mp4=create_mp4)
        else:
            self.close_video_writer()
            self.close_audio_writer()

            h264 = self.h264_file if self.h264_file in self._created_files else None
            audio = self.aud_file if self.aud_file in self._created_files else None

            if h264 and h264.exists():
                if self.video_frames == 0 or h264.stat().st_size == 0:
                    h264.unlink()
                    log.debug("Removed empty %s", h264.name)
                else:
                    log.info("Saved %s (%d video frames)", h264.name, self.video_frames)

            if audio and audio.exists():
                if self.audio_frames == 0 or audio.stat().st_size == 0:
                    audio.unlink()
                    log.debug("Removed empty %s", audio.name)
                else:
                    log.info("Saved %s (%d audio frames)", audio.name, self.audio_frames)

            if create_mp4:
                mp4_path = self.make_output_mp4_path(mp4_base)
                base = mp4_path.with_suffix("")
                out = self.mux_to_container(
                    h264 if h264 and h264.exists() else None,
                    audio if (audio and audio.exists()) else None,
                    base,
                )
                if out is not None and not self.keep_raw:
                    if h264 and h264.exists():
                        h264.unlink()
                    if audio and audio.exists():
                        if self.audio_is_aac:
                            audio.unlink()
                        else:
                            log.info("Keeping %s (raw audio)", audio.name)

        if self._mux_executor is not None:
            self._mux_executor.shutdown(wait=True)
            self._mux_executor = None
            for job in self._mux_jobs:
                job.result()
            self._mux_jobs.clear()

        if self.debug_ts and self.ts_rows:
            ts_path = self._available_path(self.out_dir / "timestamps.csv")
            try:
                with ts_path.open("w", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["stream", "type", "timestamp", "cto"])
                    w.writerows(self.ts_rows)
                log.info("Saved timestamp debug info to %s", ts_path)
            except Exception as e:
                log.error("Failed to save timestamps: %s", e)

        # Reset progress line if we were printing inline updates
        if sys.stderr.isatty() and getattr(self, "_last_progress", None) is not None:
            print("\r", end="", file=sys.stderr)
            print(file=sys.stderr)
                   

    def run(self, create_mp4: bool = True, mp4_base: Optional[str] = None):
        global_start = time.monotonic()
        deadline = global_start + self.duration if self.duration > 0 else None
        self.segment_create_mp4 = create_mp4
        ws_thread = None

        try:
            for attempt in range(1, self.max_retries + 1):
                if not self.should_run or (deadline is not None and time.monotonic() >= deadline):
                    break
                if attempt > 1:
                    log.info("Trying to reconnect (attempt %d)", attempt)

                # Let websocket-client generate exactly one Origin and subprotocol header.
                headers = dict(self.ws_headers)
                origin = next((headers.pop(k) for k in list(headers) if k.lower() == "origin"), None)
                protocol = next((headers.pop(k) for k in list(headers)
                                 if k.lower() == "sec-websocket-protocol"), "sldp.softvelum.com")
                log.debug("Using headers: %s", "; ".join(
                    f"{k}: {hide_sensitive_headers(k, v)}" for k, v in headers.items()))
                self.ws_connected = False
                self._waiting_for_keyframe = True
                self.video.sps_pps_written = False
                self.pipe_video_started = False
                self.ws = websocket.WebSocketApp(
                    self.url,
                    header=[f"{k}: {v}" for k, v in headers.items()],
                    subprotocols=[p.strip() for p in protocol.split(",")],
                    on_open=self.ws_on_open,
                    on_message=self.ws_on_message,
                    on_error=self.ws_on_error,
                    on_close=self.ws_on_close,
                )
                ws_thread = threading.Thread(
                    target=self.ws.run_forever, kwargs={"origin": origin}, daemon=True)
                ws_thread.start()
                connect_deadline = time.monotonic() + 10.0
                list_deadline = None
                connected = False
                while self.should_run and ws_thread.is_alive():
                    now = time.monotonic()
                    if deadline is not None and now >= deadline:
                        self.should_run = False
                        break
                    if self.ws_connected:
                        connected = True
                        if self.list_only and list_deadline is None:
                            list_deadline = now + 3.0
                        if not self.list_only:
                            self._print_progress(global_start, self.duration or None)
                    elif not connected and now >= connect_deadline:
                        log.error("Connection timed out")
                        break
                    if list_deadline is not None and now >= list_deadline:
                        self.should_run = False
                        break
                    time.sleep(0.05)

                self._close_websocket()
                ws_thread.join()
                ws_thread = None
                if self._callback_error is not None:
                    raise self._callback_error
                if not self.should_run or self.list_only:
                    break
                if attempt == self.max_retries:
                    raise RuntimeError(f"Connection ended after {self.max_retries} attempts")

                retry_end = time.monotonic() + self.retry_delay
                while self.should_run and time.monotonic() < retry_end:
                    if deadline is not None and time.monotonic() >= deadline:
                        self.should_run = False
                        break
                    time.sleep(0.05)
        except KeyboardInterrupt:
            log.info("Stopping...")
        finally:
            self.should_run = False
            self._close_websocket()
            if ws_thread is not None and ws_thread.ident is not None:
                ws_thread.join()
            self.finalize(create_mp4=create_mp4, mp4_base=mp4_base)
