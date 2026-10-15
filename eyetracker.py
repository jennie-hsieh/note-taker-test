from __future__ import annotations

import csv
import time
from pathlib import Path

try:
    import tobii_research as tr
except ImportError:
    tr = None


GAZE_FIELDNAMES = [
    "session_id",
    "participant_name",
    "pc_time",
    "device_time_stamp",
    "system_time_stamp",
    "phase",
    "trial_index",
    "question_number",
    "pack_id",
    "context_id",
    "sentence_id",
    "item_id",
    "mode",
    "sentence_label",
    "chunk_index",
    "chunk_text",
    "left_x",
    "left_y",
    "left_valid",
    "right_x",
    "right_y",
    "right_valid",
]


class BaseGazeRecorder:
    def __init__(self, session_id: str, participant_name: str):
        self.session_id = session_id
        self.participant_name = participant_name
        self.rows: list[dict] = []
        self.state = {
            "phase": "not_started",
            "trial_index": "",
            "question_number": "",
            "pack_id": "",
            "context_id": "",
            "sentence_id": "",
            "item_id": "",
            "mode": "",
            "sentence_label": "",
            "chunk_index": "",
            "chunk_text": "",
        }
        self.latest_x = None
        self.latest_y = None
        self.latest_valid = False

    def start(self):
        return

    def stop(self):
        return

    def set_state(self, **kwargs):
        for key, value in kwargs.items():
            if key in self.state:
                self.state[key] = value

    def clear_chunk(self):
        self.set_state(chunk_index="", chunk_text="")

    def save_csv(self, path: Path):
        if not self.rows:
            return
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=GAZE_FIELDNAMES)
            writer.writeheader()
            writer.writerows(self.rows)


class NullGazeRecorder(BaseGazeRecorder):
    pass


class TobiiGazeRecorder(BaseGazeRecorder):
    """Tobii Pro Nano recorder.

    Calibrate with Tobii Pro Eye Tracker Manager before running the experiment.
    """

    def __init__(self, session_id: str, participant_name: str):
        super().__init__(session_id, participant_name)
        self.eyetracker = None
        self.is_recording = False

    def start(self):
        if tr is None:
            raise RuntimeError("tobii_research is not installed. Run: python -m pip install tobii_research")
        found_eyetrackers = tr.find_all_eyetrackers()
        if not found_eyetrackers:
            raise RuntimeError("No Tobii eye tracker found. Check USB connection, Tobii services, and calibration.")
        self.eyetracker = found_eyetrackers[0]
        self.eyetracker.subscribe_to(tr.EYETRACKER_GAZE_DATA, self._gaze_callback, as_dictionary=True)
        self.is_recording = True
        print("Tobii connected:")
        print("  Address:", self.eyetracker.address)
        print("  Model:", self.eyetracker.model)
        print("  Serial number:", self.eyetracker.serial_number)

    def stop(self):
        if self.eyetracker is not None and self.is_recording:
            self.eyetracker.unsubscribe_from(tr.EYETRACKER_GAZE_DATA, self._gaze_callback)
            self.is_recording = False

    def _gaze_callback(self, gaze_data):
        left = gaze_data.get("left_gaze_point_on_display_area", ("", ""))
        right = gaze_data.get("right_gaze_point_on_display_area", ("", ""))
        left_valid = gaze_data.get("left_gaze_point_validity", "")
        right_valid = gaze_data.get("right_gaze_point_validity", "")

        xs = []
        ys = []
        if left_valid:
            xs.append(left[0])
            ys.append(left[1])
        if right_valid:
            xs.append(right[0])
            ys.append(right[1])
        self.latest_valid = bool(xs)
        if xs:
            self.latest_x = sum(xs) / len(xs)
            self.latest_y = sum(ys) / len(ys)

        self.rows.append({
            "session_id": self.session_id,
            "participant_name": self.participant_name,
            "pc_time": time.time(),
            "device_time_stamp": gaze_data.get("device_time_stamp", ""),
            "system_time_stamp": gaze_data.get("system_time_stamp", ""),
            **self.state,
            "left_x": left[0],
            "left_y": left[1],
            "left_valid": left_valid,
            "right_x": right[0],
            "right_y": right[1],
            "right_valid": right_valid,
        })


def create_gaze_recorder(mode: str, session_id: str, participant_name: str) -> BaseGazeRecorder:
    mode = (mode or "off").lower()
    if mode == "off":
        return NullGazeRecorder(session_id, participant_name)
    if mode == "tobii":
        return TobiiGazeRecorder(session_id, participant_name)
    raise ValueError(f"Unknown eyetracker mode: {mode}. Use off or tobii.")
