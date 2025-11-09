sldp-grabber

A small tool and Python library to grab streams from **SLDP WebSocket** servers.

- Connects to SLDP servers
- Saves raw **H.264**, **AAC**, or **Opus**
- Optionally muxes into **MP4/MKV** using `ffmpeg`
- Supports **segmenting**, **reconnects**, and **live piping** to tools like `ffplay`
- Exposes a clean `SLDPGrabber` API for custom processing


> **Note**  
> **SLDP (Softvelum Low Delay Protocol)** is designed, developed, and owned by **Softvelum LLC**.  
> This project is an independent, unofficial helper tool for working with SLDP-compatible servers (such as Nimble Streamer).  
> For official SLDP documentation and products, see: `https://softvelum.com/sldp`

---

## What is SLDP? (Context)

**SLDP (Softvelum Low Delay Protocol)** is a WebSocket-based protocol built for **low-latency live streaming**, typically targeting ~1s glass-to-glass delay or better.

It is widely used for scenarios where latency matters:

- Interactive streaming & real-time chats
- Esports / gaming streams
- Betting, auctions, and live trading
- Security & surveillance
- Second-screen / realtime companion feeds

Key properties (as defined by Softvelum):

- Runs over **WebSockets**
- Designed for **low delay** playback
- **Codec-agnostic**: H.264/H.265/AV1/VP8/VP9 video; AAC/MP3/Opus audio (depending on player/platform)
- Supports ABR-style switching and synchronized playback
- Supported via official players and SDKs (HTML5, Android, iOS, etc.)

If you are new to SLDP, start with the official docs & players on the Softvelum website.

---


Installation

From PyPI:

```bash
pip install sldp-grabber
````

From source:

```bash
git clone https://github.com/dgkn94/sldp-grabber.git
cd sldp-grabber
pip install .
```

Requirements:

* Python **3.7+**
* `websocket-client>=1.3.0`
* `ffmpeg` in `PATH` for MP4/MKV muxing (recommended)
* Optional:

  * `ffplay` for live preview
  * `av` (PyAV) for thumbnail/advanced processing examples

---

## Quick Start (CLI)

### Basic recording

```bash
sldp-grabber \
  --url wss://example.com/app/stream \
  --duration 60
```

* Writes `video.h264` / `audio.aac` into `recordings/`
* If `ffmpeg` is available and `--no-mux` is **not** set, also creates `recording.mp4`

### Keep only raw streams

```bash
sldp-grabber \
  --url wss://example.com/app/stream \
  --duration 30 \
  --no-mux \
  --keep-raw
```

### Segment recording (e.g. 5-minute chunks)

```bash
sldp-grabber \
  --url wss://example.com/app/stream \
  --segment-duration 300
```

Each segment becomes its own raw/set of files and (optionally) MP4/MKV.

---

## Live Preview with `ffplay`

You can mirror the live stream to another process while still recording.

Example: live H.264 preview while recording:

```bash
sldp-grabber \
  --url wss://example.com/app/stream \
  --pipe-h264-cmd "ffplay -fflags nobuffer -flags low_delay -f h264 -i -" \
  --duration 60
```

What this does:

* Pipes Annex B H.264 to `ffplay` via stdin
* Still writes to disk
* Still muxes to MP4 if enabled

Same idea works for audio using `--pipe-aac-cmd`.

---

## Using as a Library

You can import and use `SLDPGrabber` directly.

### Basic usage

```python
from sldp_grabber import SLDPGrabber

grabber = SLDPGrabber(
    url="wss://example.com/app/stream",
    out_dir="recordings",
    duration=30,
)

grabber.run()  # blocks until done
```

---

## Custom Frame Processing

You can hook into incoming frames using callbacks.

```python
from sldp_grabber import SLDPGrabber

class FrameProcessor:
    def __init__(self):
        self.video_count = 0
        self.audio_count = 0

    def on_video_frame(self, data: bytes, meta: dict):
        # data: Annex B H.264
        self.video_count += 1
        if meta.get("keyframe"):
            print(f"Keyframe at TS={meta.get('timestamp')}")

    def on_audio_frame(self, data: bytes, meta: dict):
        # data: ADTS AAC or raw Opus
        self.audio_count += 1

processor = FrameProcessor()

grabber = SLDPGrabber(
    url="wss://example.com/app/stream",
    out_dir="processed_recordings",
    duration=10,
    on_video_frame=processor.on_video_frame,
    on_audio_frame=processor.on_audio_frame,
)

grabber.run()

print(
    f"Processed {processor.video_count} video frames and "
    f"{processor.audio_count} audio frames"
)
```

---

## Example: Extract Thumbnails with PyAV

See `examples/extract_thumbnails.py` for a full script.

```python
import os
import av
from sldp_grabber import SLDPGrabber

class ThumbnailExtractor:
    def __init__(self, out_dir="thumbnails"):
        os.makedirs(out_dir, exist_ok=True)
        self.out_dir = out_dir
        self.count = 0
        self.codec = av.CodecContext.create("h264", "r")

    def on_video_frame(self, data, meta):
        try:
            packet = av.packet.Packet(data)
            frames = self.codec.decode(packet)
        except av.AVError:
            return

        for frame in frames:
            self.count += 1
            path = os.path.join(self.out_dir, f"thumb_{self.count:04d}.jpg")
            frame.to_image().save(path)
            print(f"Saved {path}")

extractor = ThumbnailExtractor()

grabber = SLDPGrabber(
    url="wss://example.com/app/stream",
    out_dir="thumb_recordings",
    duration=10,
    on_video_frame=extractor.on_video_frame,
    keep_raw=False,
)

grabber.run(create_mp4=False)
print(f"Saved {extractor.count} thumbnails")
```

Requires:

```bash
pip install av
```

---

## CLI Options Overview

Run:

```bash
sldp-grabber --help
```

Key options:

* `-u, --url` – **(required)** SLDP WebSocket URL
* `--stream` – Specific stream name (if multiple)
* `-d, --duration` – Seconds to record (`0` = until interrupted)
* `--out-dir` – Output directory (default: `recordings`)
* `--segment-duration` – Split into N-second segments
* `--no-mux` – Do not run `ffmpeg`, keep only raw files
* `--keep-raw` – Keep `.h264` / `.aac` / `.opus` after muxing
* `--pipe-h264-cmd` – Pipe H.264 to command via stdin
* `--pipe-aac-cmd` – Pipe AAC/Opus to command via stdin
* `-H, --header` – Extra HTTP headers (auth, cookies, etc.)
* `--header-file` – Load headers from file
* `--strict` – Fail fast on unsupported codecs
* `--debug-ts` – Save timestamps to `timestamps.csv`
* `-v` – Increase verbosity (`-v` = DEBUG)

---

## Notes

* Video is handled as H.264 (Annex B) when supported.
* Audio is handled as AAC (ADTS) or raw Opus packets.
* Muxing:

  * H.264 + AAC → MP4
  * H.264 + (no AAC) + Opus sidecar → MKV + `.opus` kept
* Designed to be simple to script against and easy to extend.
