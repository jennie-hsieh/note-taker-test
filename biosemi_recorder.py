from __future__ import annotations

import csv
import threading
import time
from pathlib import Path

try:
    from pylsl import StreamInlet, resolve_streams
except ImportError:
    StreamInlet = None
    resolve_streams = None


class NullEEGRecorder:
    def start(self):
        return

    def stop(self):
        return

    def save_csv(self, path: Path):
        return


class BioSemiLSLRecorder:
    """Optional EEG recorder for BioSemi through LSL.

    This expects BioSemi/ActiView to already be streamed to LSL, often through a
    BioSemi-to-LSL bridge or Lab Streaming Layer setup. For long/high-density
    recordings, LabRecorder/XDF is usually safer than CSV; this class is mainly
    for simple synchronized testing.
    """

    def __init__(self, stream_name: str | None = None, stream_type: str = "EEG", pull_timeout: float = 0.0):
        if StreamInlet is None or resolve_streams is None:
            raise RuntimeError("pylsl is not installed. Run: python -m pip install pylsl")
        self.stream_name = stream_name
        self.stream_type = stream_type
        self.pull_timeout = pull_timeout
        self.inlet = None
        self.rows: list[list[float]] = []
        self.channel_count = 0
        self.srate = None
        self.running = False
        self.thread: threading.Thread | None = None

    def start(self):
        streams = resolve_streams(wait_time=5.0)
        candidates = []
        for stream in streams:
            if self.stream_name and stream.name() == self.stream_name:
                candidates.append(stream)
            elif not self.stream_name and stream.type().lower() == self.stream_type.lower():
                candidates.append(stream)
        if not candidates:
            if self.stream_name:
                raise RuntimeError(f"No LSL stream found with name={self.stream_name!r}.")
            raise RuntimeError(f"No LSL stream found with type={self.stream_type!r}.")

        info = candidates[0]
        self.inlet = StreamInlet(info, max_buflen=360)
        self.channel_count = info.channel_count()
        self.srate = info.nominal_srate()
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        print("BioSemi/EEG LSL stream connected:")
        print("  Name:", info.name())
        print("  Type:", info.type())
        print("  Channels:", self.channel_count)
        print("  Nominal srate:", self.srate)

    def stop(self):
        self.running = False
        if self.thread is not None:
            self.thread.join(timeout=2)

    def _loop(self):
        while self.running:
            if self.inlet is None:
                time.sleep(0.01)
                continue
            samples, timestamps = self.inlet.pull_chunk(timeout=self.pull_timeout, max_samples=128)
            for sample, ts in zip(samples, timestamps):
                self.rows.append([time.time(), ts, *sample])
            if not samples:
                time.sleep(0.001)

    def save_csv(self, path: Path):
        if not self.rows:
            return
        fieldnames = ["pc_time", "lsl_time"] + [f"ch_{i + 1}" for i in range(self.channel_count)]
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(fieldnames)
            writer.writerows(self.rows)


def create_eeg_recorder(mode: str, stream_name: str | None = None, stream_type: str = "EEG"):
    mode = (mode or "off").lower()
    if mode == "off":
        return NullEEGRecorder()
    if mode == "lsl":
        return BioSemiLSLRecorder(stream_name=stream_name, stream_type=stream_type)
    raise ValueError(f"Unknown EEG mode: {mode}. Use off or lsl.")
