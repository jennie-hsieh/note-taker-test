"""
Replay a video-experiment gaze CSV on top of the video, with subtitles and the
eyetracker valid-area frame.

Usage:
    python replay_video_gaze.py results/<name>_gaze_<session>.csv
    python replay_video_gaze.py results/<name>_gaze_<session>.csv --video video/Riddle_2.mp4 --srt subtitles/Riddle_2.en.srt

Controls:
    SPACE = pause / resume
    LEFT  = jump back 2 seconds
    RIGHT = jump forward 2 seconds
    V     = show / hide valid-area frame
    I     = show / hide debug info
    ESC   = quit

Layout constants must match VideoStim.py. Requires gaze CSVs that contain
the video_time column (recorded by the updated VideoStim.py / eyetracker.py).
"""

from __future__ import annotations

import argparse
import bisect
import csv
import math
import time
from pathlib import Path

import srt
from psychopy import core, event, monitors, visual
from psychopy.tools.monitorunittools import pix2cm
from psychopy.visual.vlcmoviestim import VlcMovieStim


# ---- Layout (keep identical to VideoStim.py) ----
SCREEN_PX = (1920, 1080)
SCREEN_WIDTH_CM = 47.5
SCREEN_HEIGHT_CM = 30.0
VIEWING_DISTANCE_CM = 60.0

EYE_VALID_LEFT_CM = 4.0
EYE_VALID_TOP_CM = 6.5
EYE_VALID_WIDTH_CM = 37.5
EYE_VALID_HEIGHT_CM = 18.0

VIDEO_ASPECT = 16 / 9
SUB_FONT_HEIGHT_CM = 1.1
SUB_BG_HEIGHT_CM = 3.0
SUB_BOTTOM_PADDING_CM = 0.3
SUB_WIDTH_RATIO = 0.9

VALID_CENTER_X = -SCREEN_WIDTH_CM / 2 + EYE_VALID_LEFT_CM + EYE_VALID_WIDTH_CM / 2
VALID_CENTER_Y = SCREEN_HEIGHT_CM / 2 - EYE_VALID_TOP_CM - EYE_VALID_HEIGHT_CM / 2
VALID_BOTTOM_Y = VALID_CENTER_Y - EYE_VALID_HEIGHT_CM / 2

_fit_width = min(EYE_VALID_WIDTH_CM, EYE_VALID_HEIGHT_CM * VIDEO_ASPECT)
VIDEO_SIZE_CM = (_fit_width, _fit_width / VIDEO_ASPECT)

SUB_WIDTH_CM = EYE_VALID_WIDTH_CM * SUB_WIDTH_RATIO
SUB_CENTER_Y = VALID_BOTTOM_Y + SUB_BOTTOM_PADDING_CM + SUB_BG_HEIGHT_CM / 2

DEFAULT_VIDEO = Path("video/Riddle_2.mp4")
DEFAULT_SRT = Path("subtitles/Riddle_2.en.srt")


# ---- Helpers ----
def safe_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def is_valid_flag(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "valid", "yes"}


def load_gaze_rows(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    if "video_time" not in fieldnames:
        raise RuntimeError(
            "This CSV has no video_time column. Re-run the experiment with the "
            "updated VideoStim.py and eyetracker.py."
        )
    timed = [row for row in rows if safe_float(row.get("pc_time")) is not None]
    if not timed:
        raise RuntimeError("No usable rows (pc_time missing or file empty).")
    return timed


def average_gaze(row: dict):
    """Average the valid eyes. Returns normalized (x, y) or None."""
    xs, ys = [], []
    for side in ("left", "right"):
        x = safe_float(row.get(f"{side}_x"))
        y = safe_float(row.get(f"{side}_y"))
        if x is not None and y is not None and is_valid_flag(row.get(f"{side}_valid")):
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return sum(xs) / len(xs), sum(ys) / len(ys)


def gaze_to_cm(x_norm: float, y_norm: float) -> tuple[float, float]:
    """Normalized screen coords (0..1, top-left origin) -> window cm (center origin, y up)."""
    return (x_norm - 0.5) * SCREEN_WIDTH_CM, (0.5 - y_norm) * SCREEN_HEIGHT_CM


def in_valid_area(x_cm: float, y_cm: float) -> bool:
    half_w = EYE_VALID_WIDTH_CM / 2
    half_h = EYE_VALID_HEIGHT_CM / 2
    return (
        VALID_CENTER_X - half_w <= x_cm <= VALID_CENTER_X + half_w
        and VALID_CENTER_Y - half_h <= y_cm <= VALID_CENTER_Y + half_h
    )


class SubtitleLookup:
    def __init__(self, path: Path):
        with path.open("r", encoding="utf-8-sig") as handle:
            subs = sorted(srt.parse(handle.read()), key=lambda s: s.start)
        self.subs = subs
        self.starts = [s.start.total_seconds() for s in subs]

    def at(self, t: float) -> str:
        i = bisect.bisect_right(self.starts, t) - 1
        if i >= 0 and t < self.subs[i].end.total_seconds():
            return self.subs[i].content
        return ""


def fit_subtitle(win, text_stim, text: str):
    """Set subtitle text and shrink it until it fits the subtitle box."""
    text_stim.text = text
    text_stim.height = SUB_FONT_HEIGHT_CM
    for _ in range(6):
        bbox = getattr(text_stim, "boundingBox", (0, 0))
        width_cm = float(pix2cm(bbox[0], win.monitor))
        height_cm = float(pix2cm(bbox[1], win.monitor))
        if width_cm <= SUB_WIDTH_CM * 0.95 and height_cm <= SUB_BG_HEIGHT_CM - 0.4:
            break
        text_stim.height *= 0.9


def parse_args():
    parser = argparse.ArgumentParser(description="Replay video-experiment gaze with subtitles.")
    parser.add_argument("gaze_csv", type=Path, help="Gaze CSV generated by VideoStim.py.")
    parser.add_argument("--video", type=Path, default=DEFAULT_VIDEO, help="Video file. Default: video/Riddle_2.mp4")
    parser.add_argument("--srt", type=Path, default=DEFAULT_SRT, help="Subtitle file. Default: subtitles/Riddle_2.en.srt")
    parser.add_argument("--trail", type=int, default=40, help="Recent gaze rows drawn as a trail. Default: 40.")
    parser.add_argument("--hide-info", action="store_true", help="Hide the debug text overlay.")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.gaze_csv.exists():
        raise FileNotFoundError(args.gaze_csv)
    if not args.video.exists():
        raise FileNotFoundError(args.video)
    if not args.srt.exists():
        raise FileNotFoundError(args.srt)

    gaze_rows = load_gaze_rows(args.gaze_csv)
    pc_times = [safe_float(row.get("pc_time")) for row in gaze_rows]
    subs = SubtitleLookup(args.srt)

    monitor = monitors.Monitor("replay_monitor", width=SCREEN_WIDTH_CM, distance=VIEWING_DISTANCE_CM)
    monitor.setSizePix(SCREEN_PX)
    win = visual.Window(
        size=SCREEN_PX,
        fullscr=True,
        units="cm",
        color="black",
        monitor=monitor,
        checkTiming=False,
    )

    video = VlcMovieStim(
        win=win,
        filename=str(args.video),
        units="cm",
        size=VIDEO_SIZE_CM,
        pos=(VALID_CENTER_X, VALID_CENTER_Y),
        loop=False,
        autoStart=False,
    )
    subtitle_bg = visual.Rect(
        win=win, units="cm", width=SUB_WIDTH_CM, height=SUB_BG_HEIGHT_CM,
        pos=(VALID_CENTER_X, SUB_CENTER_Y), fillColor="black", lineColor=None,
        opacity=0.70, autoLog=False,
    )
    subtitle_text = visual.TextStim(
        win=win, units="cm", text="", pos=(VALID_CENTER_X, SUB_CENTER_Y),
        height=SUB_FONT_HEIGHT_CM, wrapWidth=SUB_WIDTH_CM * 0.95, color="white",
    )
    valid_frame = visual.Rect(
        win=win, units="cm", width=EYE_VALID_WIDTH_CM, height=EYE_VALID_HEIGHT_CM,
        pos=(VALID_CENTER_X, VALID_CENTER_Y), fillColor=None, lineColor="#FBBF24", lineWidth=2,
    )
    gaze_dot = visual.Circle(win, radius=0.25, units="cm", fillColor="#FF4D4D", lineColor="#FF4D4D")
    trail_dot = visual.Circle(win, radius=0.1, units="cm", fillColor="white", lineColor="white")
    info_text = visual.TextStim(
        win=win, units="cm", text="",
        pos=(-SCREEN_WIDTH_CM / 2 + 0.5, SCREEN_HEIGHT_CM / 2 - 0.5),
        anchorHoriz="left", anchorVert="top", height=0.45, color="#B8B8B8",
        wrapWidth=SCREEN_WIDTH_CM - 1,
    )

    replay_pc = pc_times[0]
    last_pc = pc_times[-1]
    paused = False
    show_info = not args.hide_info
    show_frame = True
    video_playing = False
    last_seek_time = -1.0
    shown_sub = None
    last_tick = time.perf_counter()

    try:
        while True:
            keys = event.getKeys(keyList=["escape", "space", "left", "right", "v", "i"])
            if "escape" in keys:
                break
            if "space" in keys:
                paused = not paused
            if "v" in keys:
                show_frame = not show_frame
            if "i" in keys:
                show_info = not show_info

            now = time.perf_counter()
            if not paused:
                replay_pc += now - last_tick
            last_tick = now
            if "left" in keys:
                replay_pc -= 2.0
            if "right" in keys:
                replay_pc += 2.0
            replay_pc = min(max(replay_pc, pc_times[0]), last_pc)

            # Current gaze row and matching video time.
            row_idx = max(0, bisect.bisect_right(pc_times, replay_pc) - 1)
            row = gaze_rows[row_idx]
            video_time = safe_float(row.get("video_time"), 0.0)
            phase = str(row.get("phase", ""))

            # Play/pause the video to follow the recording.
            want_playing = not paused and not phase.startswith("video_paused")
            if want_playing and not video_playing:
                video.play()
                video_playing = True
            elif not want_playing and video_playing:
                video.pause()
                video_playing = False

            # Re-sync the video if it has drifted (also handles seeks).
            if abs(video.frameTime - video_time) > 0.5 and now - last_seek_time > 0.5:
                video.seek(video_time)
                last_seek_time = now

            # Draw: video, frame, subtitles, gaze.
            video.draw()
            if show_frame:
                valid_frame.draw()

            sub = subs.at(video_time)
            if sub:
                if sub != shown_sub:
                    fit_subtitle(win, subtitle_text, sub)
                    shown_sub = sub
                subtitle_bg.draw()
                subtitle_text.draw()
            else:
                shown_sub = None

            start = max(0, row_idx - args.trail + 1)
            for idx in range(start, row_idx):
                past = average_gaze(gaze_rows[idx])
                if past is not None:
                    trail_dot.pos = gaze_to_cm(*past)
                    trail_dot.draw()

            current = average_gaze(row)
            inside = False
            if current is not None:
                x_cm, y_cm = gaze_to_cm(*current)
                inside = in_valid_area(x_cm, y_cm)
                color = "#FF4D4D" if inside else "#FBBF24"
                gaze_dot.fillColor = color
                gaze_dot.lineColor = color
                gaze_dot.pos = (x_cm, y_cm)
                gaze_dot.draw()

            if show_info:
                rec_t = pc_times[row_idx] - pc_times[0]
                info_text.text = (
                    f"row {row_idx + 1}/{len(gaze_rows)} | rec t={rec_t:.2f}s | "
                    f"video t={video_time:.2f}s | {'PAUSED' if paused else 'playing'}\n"
                    f"phase={phase} | chunk={row.get('chunk_index', '')} | "
                    f"gaze={'valid' if current is not None else 'none'} | "
                    f"in valid area={'yes' if inside else 'no'} | subtitle={'yes' if sub else 'no'}"
                )
                info_text.draw()

            win.flip(clearBuffer=True)

    finally:
        video.pause()
        win.close()
        core.quit()


if __name__ == "__main__":
    main()
