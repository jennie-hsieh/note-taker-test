from __future__ import annotations

"""
Replay Tobii gaze CSV on top of the PsychoPy display.

Example:
    python replay_gaze.py results/your_file.csv
    python replay_gaze.py results/your_file.csv --speed 2

Controls:
    SPACE = pause / resume
    LEFT  = jump back 2 seconds
    RIGHT = jump forward 2 seconds
    I     = show / hide debug info
    ESC   = quit
"""

import argparse
import csv
import json
import math
import time
from collections import deque
from pathlib import Path
from typing import Any

from psychopy import core, event, visual

from display_lsl import (
    Params,
    build_centered_rich_stims,
    build_choice_screen_with_selection,
    build_rating_screen,
    build_spaced_text_stims,
    compose_trial_context,
    create_window,
    draw_stims,
    strip_hidden_punctuation,
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SUBSET_CONTEXT_BANK_PATH = DATA_DIR / "0511_context_bank.tsv"
SUBSET_SENTENCE_BANK_PATH = DATA_DIR / "0511_sentence_bank.tsv"

try:
    from main_experiment import (
        build_practice_trials,
        load_trial_specs,
        load_tsv_rows as load_experiment_tsv_rows,
        SUBSET_CONTEXT_BANK_PATH as EXP_CONTEXT_PATH,
        SUBSET_SENTENCE_BANK_PATH as EXP_SENTENCE_PATH,
    )
except Exception:
    build_practice_trials = None
    load_trial_specs = None
    load_experiment_tsv_rows = None
    EXP_CONTEXT_PATH = SUBSET_CONTEXT_BANK_PATH
    EXP_SENTENCE_PATH = SUBSET_SENTENCE_BANK_PATH

READING_PHASE_PREFIXES = ("context_reading",)


def load_tsv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def safe_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except Exception:
        return default


def is_valid_flag(value: Any) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "valid", "yes"}


def load_trial_lookup() -> dict[tuple[str, str], dict[str, Any]]:
    """Build the same prompt/option lookup as main_experiment.py when possible."""
    if load_trial_specs is not None and build_practice_trials is not None and load_experiment_tsv_rows is not None:
        _, formal_trials = load_trial_specs()
        practice_trials = build_practice_trials(
            load_experiment_tsv_rows(EXP_CONTEXT_PATH),
            load_experiment_tsv_rows(EXP_SENTENCE_PATH),
        )
        lookup: dict[tuple[str, str], dict[str, Any]] = {}
        for trial in practice_trials + formal_trials:
            lookup[(str(trial["context_id"]), str(trial["sentence_id"]))] = dict(trial)
        return lookup

    if not SUBSET_CONTEXT_BANK_PATH.exists() or not SUBSET_SENTENCE_BANK_PATH.exists():
        raise RuntimeError(
            "Missing data TSV files. Expected:\n"
            f"  {SUBSET_CONTEXT_BANK_PATH}\n"
            f"  {SUBSET_SENTENCE_BANK_PATH}"
        )
    context_rows = load_tsv_rows(SUBSET_CONTEXT_BANK_PATH)
    sentence_rows = load_tsv_rows(SUBSET_SENTENCE_BANK_PATH)
    context_by_id = {str(row["context_id"]): row for row in context_rows}

    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for sentence_row in sentence_rows:
        context_id = str(sentence_row["context_id"])
        sentence_id = str(sentence_row["sentence_id"])
        context_row = context_by_id.get(context_id)
        if context_row is None:
            continue
        options = json.loads(context_row.get("comprehension_options_json") or "[]")
        lookup[(context_id, sentence_id)] = {
            "question_number": None,
            "pack_id": "",
            "context_id": context_id,
            "sentence_id": sentence_id,
            "source_table": str(context_row.get("source_table", "")),
            "item_id": str(context_row.get("item_id", "")),
            "mode": str(context_row.get("mode", "")),
            "label": str(sentence_row.get("label", "")),
            "target_sentence": str(sentence_row.get("target_sentence", "")),
            "displayed_context_before": str(context_row.get("displayed_context_before", "")),
            "displayed_context_after": str(context_row.get("displayed_context_after", "")),
            "full_context": str(context_row.get("full_context", "")),
            "context_question_prompt": str(context_row.get("comprehension_prompt", "")),
            "context_question_options": options[:4],
            "context_question_correct": str(context_row.get("comprehension_correct", "")),
        }
    return lookup

def get_trial_for_row(row: dict[str, str], trial_lookup: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any] | None:
    context_id = str(row.get("context_id", "")).strip()
    sentence_id = str(row.get("sentence_id", "")).strip()
    trial = trial_lookup.get((context_id, sentence_id))
    if trial is None:
        return None
    trial = dict(trial)
    qn = safe_int(row.get("question_number"), None)
    if qn is not None:
        trial["question_number"] = qn
    trial["pack_id"] = str(row.get("pack_id", ""))
    return trial


def average_gaze(row: dict[str, str]) -> tuple[float | None, float | None, bool]:
    lx = safe_float(row.get("left_x"), None)
    ly = safe_float(row.get("left_y"), None)
    rx = safe_float(row.get("right_x"), None)
    ry = safe_float(row.get("right_y"), None)
    left_valid = is_valid_flag(row.get("left_valid")) and lx is not None and ly is not None
    right_valid = is_valid_flag(row.get("right_valid")) and rx is not None and ry is not None

    xs: list[float] = []
    ys: list[float] = []
    if left_valid:
        xs.append(float(lx))
        ys.append(float(ly))
    if right_valid:
        xs.append(float(rx))
        ys.append(float(ry))

    if not xs:
        return None, None, False
    return sum(xs) / len(xs), sum(ys) / len(ys), True


def norm_to_pix(win, x_norm: float, y_norm: float) -> tuple[float, float]:
    width, height = win.size
    x_pix = (x_norm - 0.5) * width
    y_pix = (0.5 - y_norm) * height
    return x_pix, y_pix


class ReplayStimCache:
    def __init__(self, win, params: Params):
        self.win = win
        self.params = params
        self.cache: dict[tuple[Any, ...], list[Any]] = {}

    def get_for_row(self, row: dict[str, str], trial: dict[str, Any] | None) -> tuple[list[Any], str]:
        phase = str(row.get("phase", "") or "")

        if trial is None:
            return self._message_stims(f"Missing trial\nphase: {phase}"), "missing_trial"

        if phase.startswith(READING_PHASE_PREFIXES):
            return self._reading_stims(row, trial, phase)
        if phase == "qa_screen":
            return self._qa_stims(trial)
        if phase == "rating_screen":
            return self._rating_stims(trial, show_title=(str(row.get("pack_id", "")) == "practice"))
        if phase == "fixation":
            return self._fixation_stims(), "fixation"
        if phase == "trial_cue":
            return self._cue_stims(trial), "trial_cue"
        if phase == "break":
            return self._message_stims("Break"), "break"
        if phase in {"inter_trial_blank", "rest"}:
            return [], "blank"
        return self._message_stims(phase or "unknown_phase"), "message"

    def _reading_stims(self, row: dict[str, str], trial: dict[str, Any], phase: str) -> tuple[list[Any], str]:
        chunk_text = str(row.get("chunk_text", "")).strip()
        if not chunk_text:
            return [], "reading_blank"
        chunk_text = strip_hidden_punctuation(chunk_text)
        key = ("reading", trial.get("context_id"), trial.get("sentence_id"), safe_int(row.get("chunk_index"), -1), chunk_text)
        if key not in self.cache:
            self.cache[key] = build_spaced_text_stims(
                self.win,
                chunk_text,
                font_height=self.params.font_height,
                pos=(0, 0),
                char_spacing_cm=self.params.char_spacing_cm,
                line_spacing_cm=self.params.line_spacing_cm,
                base_color="#FFFFFF",
            )
        return self.cache[key], "reading"

    def _qa_stims(self, trial: dict[str, Any]) -> tuple[list[Any], str]:
        key = ("qa", trial.get("context_id"), trial.get("sentence_id"))
        if key not in self.cache:
            self.cache[key] = build_choice_screen_with_selection(
                self.win,
                trial["context_question_prompt"],
                trial["context_question_options"],
                selected_index=0,
                params=self.params,
            )
        return self.cache[key], "qa"

    def _rating_stims(self, trial: dict[str, Any], show_title: bool) -> tuple[list[Any], str]:
        key = ("rating", trial.get("context_id"), trial.get("sentence_id"), show_title)
        if key not in self.cache:
            trial_context = compose_trial_context(
                trial["displayed_context_before"],
                trial["target_sentence"],
                trial["displayed_context_after"],
            )
            self.cache[key] = build_rating_screen(
                self.win,
                trial_context,
                trial["target_sentence"],
                rating=4,
                params=self.params,
                show_title=show_title,
            )
        return self.cache[key], "rating"

    def _fixation_stims(self) -> list[Any]:
        key = ("fixation",)
        if key not in self.cache:
            fixation = visual.TextStim(
                self.win,
                text="+",
                font="Microsoft JhengHei",
                units="cm",
                pos=(0, 0),
                height=1.8,
                color="#FF4D4D",
            )
            self.cache[key] = [fixation]
        return self.cache[key]

    def _cue_stims(self, trial: dict[str, Any]) -> list[Any]:
        mode = str(trial.get("mode", "")).upper()
        if mode == "HIGH":
            lines = [
                [("請", "#FFFFFF"), ("仔細", "#FBBF24"), ("地閱讀每一個字，接著回答一題", "#FFFFFF"), ("選擇題", "#FBBF24")],
            ]
        elif mode == "LOW":
            lines = [
                [("請", "#FFFFFF"), ("輕鬆", "#FBBF24"), ("地閱讀，接著回答一題", "#FFFFFF"), ("是非題", "#FBBF24")],
            ]
        else:
            lines = [[("請閱讀，接著回答一題問題", "#FFFFFF")]]
        key = ("cue", mode)
        if key not in self.cache:
            stims = []
            wrapped_lines = []
            for line_segments in lines:
                current_line = []
                for text, color in line_segments:
                    parts = text.split("，")
                    for idx, part in enumerate(parts):
                        if part:
                            current_line.append((part, color))
                        if idx < len(parts) - 1:
                            wrapped_lines.append(current_line)
                            current_line = []
                if current_line:
                    wrapped_lines.append(current_line)
            line_gap = self.params.font_height + 0.6
            start_y = (len(wrapped_lines) - 1) * line_gap / 2
            for idx, line_segments in enumerate(wrapped_lines):
                stims.extend(
                    build_centered_rich_stims(
                        self.win,
                        line_segments,
                        self.params.font_height,
                        (0, start_y - idx * line_gap),
                        self.params.char_spacing_cm,
                    )
                )
            self.cache[key] = stims
        return self.cache[key]

    def _message_stims(self, text: str) -> list[Any]:
        key = ("message", text)
        if key not in self.cache:
            self.cache[key] = build_spaced_text_stims(
                self.win,
                text,
                font_height=1.0,
                pos=(0, 0),
                char_spacing_cm=0.18,
                line_spacing_cm=0.28,
                base_color="#FFFFFF",
            )
        return self.cache[key]


def parse_args():
    parser = argparse.ArgumentParser(description="Replay Tobii gaze over the timed refactored PsychoPy display.")
    parser.add_argument("gaze_csv", type=Path, help="Path to gaze CSV generated by the experiment.")
    parser.add_argument("--speed", type=float, default=1.0, help="Replay speed. 2 = twice as fast. Default: 1.")
    parser.add_argument("--fullscreen", action="store_true", help="Replay in fullscreen mode.")
    parser.add_argument("--start-row", type=int, default=0, help="Start replay from this gaze CSV row index.")
    parser.add_argument("--hide-info", action="store_true", help="Hide debug text overlay.")
    parser.add_argument("--trail", type=int, default=40, help="Number of recent gaze samples to show as trail. Default: 40.")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.speed <= 0:
        raise ValueError("--speed must be greater than 0")
    if not args.gaze_csv.exists():
        raise FileNotFoundError(args.gaze_csv)

    gaze_rows = load_csv_rows(args.gaze_csv)
    timed_rows = [row for row in gaze_rows if safe_float(row.get("pc_time"), None) is not None]
    if not timed_rows:
        raise RuntimeError("No usable pc_time column found in the gaze CSV.")

    start_row = max(0, min(args.start_row, len(timed_rows) - 1))
    timed_rows = timed_rows[start_row:]
    trial_lookup = load_trial_lookup()

    params = Params(fullscr=args.fullscreen)
    win = create_window(params)
    stim_cache = ReplayStimCache(win, params)

    gaze_dot = visual.Circle(win, radius=11, fillColor="red", lineColor="red", units="pix")
    trail_dot = visual.Circle(win, radius=4, fillColor="white", lineColor="white", units="pix")
    info_text = visual.TextStim(
        win,
        text="",
        units="pix",
        pos=(-win.size[0] / 2 + 20, win.size[1] / 2 - 30),
        anchorHoriz="left",
        anchorVert="top",
        height=22,
        color="#B8B8B8",
    )

    show_info = not args.hide_info
    paused = False
    row_idx = 0
    base_pc_time = safe_float(timed_rows[0].get("pc_time"), 0.0) or 0.0
    replay_start = time.perf_counter()
    pause_started: float | None = None
    gaze_trail: deque[tuple[float, float]] = deque(maxlen=max(0, args.trail))

    try:
        while row_idx < len(timed_rows):
            keys = event.getKeys()
            if "escape" in keys:
                break
            if "i" in keys:
                show_info = not show_info
            if "space" in keys:
                paused = not paused
                if paused:
                    pause_started = time.perf_counter()
                else:
                    if pause_started is not None:
                        replay_start += time.perf_counter() - pause_started
                    pause_started = None
                event.clearEvents()

            if "right" in keys:
                current_time = safe_float(timed_rows[row_idx].get("pc_time"), base_pc_time) or base_pc_time
                target = current_time + 2.0
                while row_idx < len(timed_rows) - 1 and (safe_float(timed_rows[row_idx].get("pc_time"), base_pc_time) or base_pc_time) < target:
                    row_idx += 1
                base_pc_time = safe_float(timed_rows[row_idx].get("pc_time"), base_pc_time) or base_pc_time
                replay_start = time.perf_counter()
                gaze_trail.clear()

            if "left" in keys:
                current_time = safe_float(timed_rows[row_idx].get("pc_time"), base_pc_time) or base_pc_time
                target = current_time - 2.0
                while row_idx > 0 and (safe_float(timed_rows[row_idx].get("pc_time"), base_pc_time) or base_pc_time) > target:
                    row_idx -= 1
                base_pc_time = safe_float(timed_rows[row_idx].get("pc_time"), base_pc_time) or base_pc_time
                replay_start = time.perf_counter()
                gaze_trail.clear()

            if not paused:
                elapsed_recorded = (time.perf_counter() - replay_start) * args.speed
                target_pc_time = base_pc_time + elapsed_recorded
                while row_idx < len(timed_rows) - 1:
                    next_time = safe_float(timed_rows[row_idx + 1].get("pc_time"), None)
                    if next_time is None or next_time > target_pc_time:
                        break
                    row_idx += 1

            row = timed_rows[row_idx]
            trial = get_trial_for_row(row, trial_lookup)
            stims, screen_kind = stim_cache.get_for_row(row, trial)
            draw_stims(win, stims)

            x_norm, y_norm, valid = average_gaze(row)
            if valid and x_norm is not None and y_norm is not None:
                x_pix, y_pix = norm_to_pix(win, x_norm, y_norm)
                gaze_trail.append((x_pix, y_pix))
                for tx, ty in gaze_trail:
                    trail_dot.pos = (tx, ty)
                    trail_dot.draw()
                gaze_dot.pos = (x_pix, y_pix)
                gaze_dot.draw()

            if show_info:
                trial_index = row.get("trial_index", "")
                qn = row.get("question_number", "")
                phase = row.get("phase", "")
                chunk_index = row.get("chunk_index", "")
                chunk_text = row.get("chunk_text", "")
                replay_time = (safe_float(row.get("pc_time"), base_pc_time) or base_pc_time) - base_pc_time
                info_text.text = (
                    f"row {start_row + row_idx + 1}/{start_row + len(timed_rows)} | "
                    f"t={replay_time:.2f}s | speed={args.speed:g}x | {'PAUSED' if paused else 'playing'}\n"
                    f"screen={screen_kind} | trial={trial_index} | Q={qn} | phase={phase} | chunk={chunk_index}\n"
                    f"chunk_text={chunk_text}"
                )
                info_text.draw()

            win.flip(clearBuffer=True)
            core.wait(0.001)

    finally:
        win.close()
        core.quit()


if __name__ == "__main__":
    main()
