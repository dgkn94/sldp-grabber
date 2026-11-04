import io
import base64
import hashlib
import json
import re
import shutil
import socketserver
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from sldp_grabber import SLDPGrabber, StreamInfo
from sldp_grabber.utils import load_headers_from_file


class GrabberTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.g = SLDPGrabber("wss://example.invalid/live", out_dir=str(self.root))
        self.addCleanup(self.g.finalize, False, None)

    def video_ready(self):
        self.g.video.stream_name = "live"
        self.g.video.extradata = b"config"
        self.g.video.sps_pps_data = b"\x00\x00\x00\x01\x67"

    def video_frame(self, keyframe=False, timestamp=0):
        kind = self.g.AVC_KEYFRAME if keyframe else self.g.AVC_FRAME
        self.g.process_video_frame(kind, timestamp, 0, b"\x00\x00\x00\x02\x65\x01")

    def test_header_file(self):
        path = self.root / "headers.txt"
        path.write_text("# comment\nAuthorization: Bearer token\n X-Test : a:b\n")
        self.assertEqual(load_headers_from_file(str(path)),
                         {"Authorization": "Bearer token", "X-Test": "a:b"})

    def test_strict_rejects_both_unsupported_codecs(self):
        self.g.strict = True
        for info in ({"vcodec": "vp9"}, {"acodec": "vorbis"}):
            with self.subTest(info=info), self.assertRaises(RuntimeError):
                self.g.parse_stream_info({"info": [{"stream": "live", "stream_info": info}]})

    def test_auto_selection_does_not_mix_renditions(self):
        self.g.parse_stream_info({"info": [
            {"stream": "first", "stream_info": {"vcodec": "avc1.64001f"}},
            {"stream": "second", "stream_info": {"acodec": "mp4a.40.2"}},
        ]})
        self.assertEqual(self.g.video.stream_name, "first")
        self.assertIsNone(self.g.audio.stream_name)

    def test_fps_counts_intervals(self):
        stream = StreamInfo(True)
        stream.frame_count, stream.first_ts, stream.last_ts = 31, 0, 1000
        self.assertEqual(self.g.estimate_fps(stream), 30)

    def test_truncated_binary_frames_are_not_delivered(self):
        self.g.process_video_frame = Mock()
        for data in (b"\x00\x03", b"\x00\x03" + bytes(8), b"\x00\x03" + bytes(11)):
            self.g.handle_binary_message(data)
        self.g.process_video_frame.assert_not_called()

    def test_annexb_rejects_partial_access_units(self):
        self.assertEqual(self.g.convert_to_annexb(b"\x02\x65\x01", 1), b"\0\0\0\1\x65\x01")
        for data in (b"\x02\x65\x01\x03\x41", b"\x00", b"\x02\x65"):
            self.assertEqual(self.g.convert_to_annexb(data, 1), b"")

    def test_truncated_parameter_sets_are_rejected(self):
        for config in (b"\x01\x64\x00\x1f\xff\xe1\x00\x01\x67",
                       b"\x01\x64\x00\x1f\xff\xe1\x00\x01\x67\x01\x00\x02\x68"):
            self.assertEqual(self.g.extract_sps_pps(config), b"")

    def test_aac_channel_configuration_is_preserved(self):
        rate, channels = self.g.parse_audio_config(bytes((0x12, 0x38)))
        header = self.g.make_adts_header(10, rate, channels)
        self.assertEqual(((header[2] & 1) << 2) | (header[3] >> 6), 7)

    def test_waits_for_keyframe_and_callback_includes_configuration(self):
        self.video_ready()
        self.g.video_writer = io.BytesIO()
        self.g.on_video_frame = Mock()
        self.video_frame()
        self.assertEqual(self.g.video_frames, 0)
        self.video_frame(True)
        self.assertEqual(self.g.video_frames, 1)
        self.assertTrue(self.g.on_video_frame.call_args[0][0].startswith(self.g.video.sps_pps_data))

    def test_rollover_waits_for_keyframe_and_resets_timing(self):
        self.video_ready()
        self.g.segment_duration = 1
        with patch("sldp_grabber.grabber.time.monotonic", return_value=0):
            self.g.open_segment_outputs()
            self.video_frame(True)
        with patch("sldp_grabber.grabber.time.monotonic", return_value=2):
            self.video_frame(timestamp=100)
            self.assertEqual(self.g.segment_index, 1)
            self.video_frame(True, timestamp=200)
        self.assertEqual(self.g.segment_index, 2)
        self.assertEqual(self.g.video.frame_count, 1)
        self.assertEqual(self.g.video.first_ts, 200)
        self.assertEqual(self.g.video_frames, 1)

    def test_audio_only_rollover(self):
        self.g.audio.stream_name = "live"
        self.g.audio_is_aac = True
        self.g.audio.extradata = b"\x12\x10"
        self.g.segment_duration = 1
        with patch("sldp_grabber.grabber.time.monotonic", return_value=0):
            self.g.open_segment_outputs()
            self.g.process_audio_frame(1, 0, b"first")
        with patch("sldp_grabber.grabber.time.monotonic", return_value=2):
            self.g.process_audio_frame(1, 2000, b"second")
        self.assertEqual(self.g.segment_index, 2)
        self.assertEqual(self.g.audio_frames, 1)

    def test_finalize_does_not_touch_existing_recordings(self):
        for name in ("video.h264", "audio.aac", "audio.opus"):
            (self.root / name).write_bytes(b"previous recording")
        self.g.finalize(False, None)
        for path in self.root.iterdir():
            self.assertEqual(path.read_bytes(), b"previous recording")

    def test_existing_outputs_are_preserved(self):
        self.video_ready()
        self.g.ws = Mock()
        (self.root / "video.h264").write_bytes(b"old")
        self.g.request_streams()
        self.assertEqual(self.g.h264_file.name, "video(1).h264")
        self.video_frame(True)
        self.g.finalize(False, None)
        self.assertEqual((self.root / "video.h264").read_bytes(), b"old")
        (self.root / "segment_0001.mp4").write_bytes(b"old")
        self.g.open_segment_outputs()
        self.assertEqual(self.g.segment_index, 2)

    def test_muxing_does_not_block_rollover(self):
        self.video_ready()
        self.g.open_segment_outputs()
        self.video_frame(True)
        entered, release = threading.Event(), threading.Event()

        def slow_mux(*args):
            entered.set()
            release.wait(5)
            return None

        with patch.object(SLDPGrabber, "mux_to_container", side_effect=slow_mux):
            try:
                self.g.finish_current_segment(True)
                self.assertTrue(entered.wait(2))
                self.g.open_segment_outputs()
                self.video_frame(True)
                self.assertEqual(self.g.video_frames, 1)
            finally:
                release.set()
                self.g._mux_executor.shutdown(wait=True)

    def test_duplicate_status_does_not_restart_pipes(self):
        self.video_ready()
        self.g.ws = Mock()
        with patch.object(self.g, "_start_pipe", return_value=Mock()) as start:
            self.g.request_streams()
            self.g.request_streams()
        self.assertEqual(start.call_count, 2)
        self.g.h264_pipe = self.g.aac_pipe = None

    def test_callback_error_stops_session(self):
        self.g.strict = True
        ws = Mock()
        self.g.ws_on_message(ws, json.dumps({"command": "status", "info": [
            {"stream": "live", "stream_info": {"vcodec": "vp9"}}]}))
        self.assertFalse(self.g.should_run)
        self.assertIsInstance(self.g._callback_error, RuntimeError)
        ws.close.assert_called_once()


class RunTests(unittest.TestCase):
    def test_local_websocket_handshake_and_duration(self):
        requests = []

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(5)
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = self.request.recv(4096)
                    if not chunk:
                        return
                    data += chunk
                lines = data.decode().split("\r\n")
                requests.extend(lines)
                key = next(line.split(":", 1)[1].strip() for line in lines
                           if line.lower().startswith("sec-websocket-key:"))
                accept = base64.b64encode(hashlib.sha1(
                    (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
                self.request.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                                      "Connection: Upgrade\r\nSec-WebSocket-Protocol: sldp.softvelum.com\r\n"
                                      f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
                self.request.recv(4096)
                self.request.sendall(b"\x88\x00")

        with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server, \
             tempfile.TemporaryDirectory() as root:
            worker = threading.Thread(target=server.handle_request, daemon=True)
            worker.start()
            g = SLDPGrabber(f"ws://127.0.0.1:{server.server_address[1]}/live", out_dir=root, duration=1)
            start = time.monotonic()
            with patch.dict("os.environ", {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
                g.run(False)
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertLess(time.monotonic() - start, 3)
            self.assertEqual(sum(line.lower().startswith("origin:") for line in requests), 1)
            self.assertEqual(sum(line.lower().startswith("sec-websocket-protocol:") for line in requests), 1)

    def test_failed_connections_raise_and_finalize(self):
        with tempfile.TemporaryDirectory() as root:
            g = SLDPGrabber("wss://example.invalid", out_dir=root, max_retries=2, retry_delay=0)
            with patch("sldp_grabber.grabber.websocket.WebSocketApp") as app, patch.object(g, "finalize") as final:
                with self.assertRaises(RuntimeError):
                    g.run(False)
                self.assertEqual(app.call_count, 2)
                final.assert_called_once_with(create_mp4=False, mp4_base=None)

    def test_duration_bounds_connection_wait(self):
        with tempfile.TemporaryDirectory() as root:
            g = SLDPGrabber("wss://example.invalid", out_dir=root, duration=1)
            now = [0.0]

            def advance(seconds):
                now[0] += seconds

            with patch("sldp_grabber.grabber.websocket.WebSocketApp") as app, \
                 patch("sldp_grabber.grabber.threading.Thread"), \
                 patch("sldp_grabber.grabber.time.monotonic", side_effect=lambda: now[0]), \
                 patch("sldp_grabber.grabber.time.sleep", side_effect=advance):
                g.run(False)
                self.assertEqual(app.call_count, 1)
                self.assertLess(now[0], 1.1)

    def test_keyboard_interrupt_during_connection_finalizes(self):
        with tempfile.TemporaryDirectory() as root:
            g = SLDPGrabber("wss://example.invalid", out_dir=root)
            with patch("sldp_grabber.grabber.websocket.WebSocketApp"), patch.object(g, "finalize") as final:
                with patch("sldp_grabber.grabber.threading.Thread.start", side_effect=KeyboardInterrupt):
                    with patch("sldp_grabber.grabber.threading.Thread.join"):
                        g.run(False)
                final.assert_called_once()

    def test_handshake_uses_one_origin_and_negotiated_subprotocol(self):
        with tempfile.TemporaryDirectory() as root:
            g = SLDPGrabber("wss://example.invalid", out_dir=root, list_only=True,
                            headers={"origin": "https://custom.invalid"})
            with patch("sldp_grabber.grabber.websocket.WebSocketApp") as app:
                g.run(False)
                options = app.call_args[1]
                self.assertFalse(any(h.lower().startswith("origin:") for h in options["header"]))
                self.assertFalse(any(h.lower().startswith("sec-websocket-protocol:") for h in options["header"]))
                self.assertEqual(options["subprotocols"], ["sldp.softvelum.com"])
                app.return_value.run_forever.assert_called_once_with(origin="https://custom.invalid")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class MediaTests(unittest.TestCase):
    def test_recorded_aac_decodes(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "source.aac"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "sine=frequency=440:sample_rate=44100", "-t", "1",
                            "-ac", "2", "-c:a", "aac", "-f", "adts", str(source)], check=True)
            g = SLDPGrabber("wss://example.invalid", out_dir=str(root / "recordings"))
            g.ws = Mock()
            g.audio.stream_name = "live"
            g.audio_is_aac = True
            data = source.read_bytes()
            try:
                g.request_streams()
                g.process_audio_frame(g.AAC_HEADER, None, b"\x12\x10")
                pos = 0
                while pos < len(data):
                    size = ((data[pos + 3] & 3) << 11) | (data[pos + 4] << 3) | (data[pos + 5] >> 5)
                    g.process_audio_frame(g.AAC_FRAME, 0, data[pos + 7:pos + size])
                    pos += size
            finally:
                g.finalize(True, "audio.test")
            output = root / "recordings" / "audio.mp4"
            self.assertTrue(output.exists())
            result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(output),
                                     "-f", "null", "-"], capture_output=True, check=True)
            self.assertEqual(result.stderr, b"")

    def test_recorded_segments_decode_independently(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "source.h264"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "testsrc=size=64x64:rate=10", "-t", "2", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-g", "5", "-bf", "0",
                            "-x264-params", "aud=1:repeat-headers=1", "-f", "h264", str(source)], check=True)
            nals = [n for n in re.split(b"\x00\x00\x00\x01|\x00\x00\x01", source.read_bytes()) if n]
            sps = next(n for n in nals if n[0] & 31 == 7)
            pps = next(n for n in nals if n[0] & 31 == 8)
            avcc = (b"\x01" + sps[1:4] + b"\xff\xe1" + struct.pack(">H", len(sps)) + sps
                    + b"\x01" + struct.pack(">H", len(pps)) + pps)
            packets = []
            for nal in nals:
                if nal[0] & 31 == 9:
                    packets.append([])
                if packets and nal[0] & 31 not in (7, 8):
                    packets[-1].append(nal)
            g = SLDPGrabber("wss://example.invalid", out_dir=str(root / "recordings"), segment_duration=0.5)
            g.video.stream_name = "live"
            g.segment_create_mp4 = True
            try:
                with patch("sldp_grabber.grabber.time.monotonic", return_value=0):
                    g.open_segment_outputs()
                g.process_video_frame(g.AVC_HEADER, None, None, avcc)
                for i, packet in enumerate(packets):
                    keyframe = any(n[0] & 31 == 5 for n in packet)
                    payload = b"".join(struct.pack(">I", len(n)) + n for n in packet)
                    with patch("sldp_grabber.grabber.time.monotonic", return_value=i / 10):
                        g.process_video_frame(g.AVC_KEYFRAME if keyframe else g.AVC_FRAME, i * 100, 0, payload)
            finally:
                g.finalize(True, None)
            outputs = sorted((root / "recordings").glob("*.mp4"))
            self.assertEqual(len(outputs), 4)
            total = 0
            for output in outputs:
                result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(output),
                                         "-f", "null", "-"], capture_output=True, check=True)
                self.assertEqual(result.stderr, b"")
                probe = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_streams",
                                        "-of", "json", str(output)], capture_output=True, text=True, check=True)
                total += int(json.loads(probe.stdout)["streams"][0]["nb_read_frames"])
            self.assertEqual(total, 20)


if __name__ == "__main__":
    unittest.main()
