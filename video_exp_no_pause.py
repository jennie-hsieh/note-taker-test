import argparse
import faulthandler
import sys
from pathlib import Path
from datetime import datetime
from datetime import time as dtime

import pandas as pd
import srt
from psychopy import visual, core, monitors, event
from psychopy.visual.vlcmoviestim import VlcMovieStim
from psychopy.hardware import keyboard
from psychopy.tools.monitorunittools import pix2cm
from pylsl import StreamInfo, StreamOutlet, local_clock

from eyetracker import create_gaze_recorder
from biosemi_recorder import create_eeg_recorder
from mirror_display import MirrorController, resolve_screen_configuration
from types import SimpleNamespace


def switch_to_english_input_method():
    """Windows only: switch the keyboard layout to US English (KLID 00000409).
    Silently does nothing on other systems or if the switch fails."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        layout = user32.LoadKeyboardLayoutW("00000409", 1)
        if layout:
            user32.ActivateKeyboardLayout(layout, 0)
    except Exception:
        return


class TeeStream:
    """Mirror a stream (stdout / stderr) to a log file, stamping the start of every line with the time."""

    def __init__(self, stream, log_file):
        self._stream = stream
        self._log = log_file
        self._at_line_start = True

    def write(self, text):
        try:
            if self._stream is not None:
                self._stream.write(text)
        except Exception:
            pass
        if text:
            out = []
            for part in text.splitlines(keepends=True):
                if self._at_line_start:
                    out.append(datetime.now().strftime("[%H:%M:%S.%f")[:-3] + "] ")
                out.append(part)
                self._at_line_start = part.endswith("\n")
            self._log.write("".join(out))
            self._log.flush()
        return len(text)

    def flush(self):
        try:
            if self._stream is not None:
                self._stream.flush()
        except Exception:
            pass
        self._log.flush()

    def __getattr__(self, name):  # isatty(), encoding, fileno() ... come from the real stream
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._stream, name)


def setup_logging(log_path: Path):
    """Save everything printed to the terminal (stdout + stderr, including tracebacks) to a txt file.
    Stays installed until the process exits, so an uncaught error is captured too."""
    log_file = log_path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = TeeStream(sys.__stdout__, log_file)
    sys.stderr = TeeStream(sys.__stderr__, log_file)
    faulthandler.enable(file=log_file)  # also records hard crashes (e.g. VLC / driver segfaults)
    return log_file


### Settings
# Display: the Tobii gaze coordinates map to the WHOLE screen,
# so the window must be fullscreen at the real display resolution.
SCREEN_PX = (1680, 1050)          # set to your display resolution
PARTICIPANT_SCREEN = 1            # PsychoPy screen index for the participant display (experimenter = 0)
SCREEN_WIDTH_CM = 47.5
SCREEN_HEIGHT_CM = 30.0
VIEWING_DISTANCE_CM = 60.0
FULLSCR = True

# Eyetracker valid area (cm, measured from the screen's top-left corner)
EYE_VALID_LEFT_CM = 4.0
EYE_VALID_TOP_CM = 6.5
EYE_VALID_WIDTH_CM = 37.5
EYE_VALID_HEIGHT_CM = 18.0

VIDEO_ASPECT = 16 / 9             # width / height of the video file

# Video number: selects video/V<nnn>.mp4, chunk/V<nnn>.xlsx, subtitles/V<nnn>.<lang>.srt
# and the LSL marker prefix. Use --id to pick a video from the terminal (1-83).
RIDDLE_NO = 2
VIDEO_ID_MIN = 0
VIDEO_ID_MAX = 83

# Subtitles: "zh-TW", "en", or None to turn subtitles off
SUB_LANG = "zh-TW"
# Keep subtitle background visible across tiny subtitle gaps to avoid blinking (sec)
SUBTITLE_BG_GAP = 0.75

# Chunk markers (no pause, no rating: the video plays through to the end)
CHUNK_TIME_SHIFT = 0              # seconds; positive sends the chunk markers earlier
# Column (0-based) of the chunk file that holds each chunk's START time, e.g. 2 = 3rd column.
# None = a chunk starts where the previous one ended (the first chunk starts at video start).
CHUNK_START_COL = None

# Resting baseline right after Space is pressed (before the video):
# 30 s eyes open (black screen + "+")  ->  5 s cue text  ->  30 s eyes closed (black screen + "+")
BASELINE_FIXATION_S = 30          # eyes-open fixation duration (sec)
BASELINE_CUE_S = 5.0              # cue text duration (sec)
BASELINE_EYES_CLOSED_S = 30       # eyes-closed duration (sec)
EYES_CLOSED_SHOW_FIXATION = True  # keep the "+" on screen during the eyes-closed period
FIXATION_POS_CM = (0, 0)          # screen center; use (VALID_CENTER_X, VALID_CENTER_Y) for the video center
FIXATION_HEIGHT_CM = 2.0
CUE_HEIGHT_CM = 1.4
CUE_EYES_CLOSED_TEXT = "請閉上眼睛\nClose your eyes"

# Markers sent at fixed video times (sec): {video number: [(time, label), ...]}
VIDEO_MARKERS = {
    0: [
        (45.177, "Fixation_Start"),
        (76.362, "Fixation_End"),
        (76.362, "EyesClosed_Start"),
        (106.359, "EyesClosed_End"),
    ],
}

# Subtitle layout
SUB_FONT_HEIGHT_CM = 1.1
SUB_BG_HEIGHT_CM = 3.0
SUB_BOTTOM_PADDING_CM = 0.3
SUB_WIDTH_RATIO = 0.9             # fraction of the valid-area width

RESULTS_DIR = Path("results")

### Derived geometry (cm, origin = screen center, y up)
VALID_CENTER_X = -SCREEN_WIDTH_CM / 2 + EYE_VALID_LEFT_CM + EYE_VALID_WIDTH_CM / 2
VALID_CENTER_Y = SCREEN_HEIGHT_CM / 2 - EYE_VALID_TOP_CM - EYE_VALID_HEIGHT_CM / 2
VALID_BOTTOM_Y = VALID_CENTER_Y - EYE_VALID_HEIGHT_CM / 2

_fit_width = min(EYE_VALID_WIDTH_CM, EYE_VALID_HEIGHT_CM * VIDEO_ASPECT)
VIDEO_SIZE_CM = (_fit_width, _fit_width / VIDEO_ASPECT)

SUB_WIDTH_CM = EYE_VALID_WIDTH_CM * SUB_WIDTH_RATIO
SUB_CENTER_Y = VALID_BOTTOM_Y + SUB_BOTTOM_PADDING_CM + SUB_BG_HEIGHT_CM / 2


### Video Stim Funcs
# Handling the subtitles
class SrtHandler:
    def __init__(self, srt_file_path: Path):
        with srt_file_path.open("r", encoding="utf-8-sig") as f:
            subtitle_text = f.read()
        self.subtitles = list(srt.parse(subtitle_text))
        self.subtitles = list(srt.sort_and_reindex(self.subtitles))  # start index = 1
        self.index = 0

    def getSubtitleState(self, currentTime):
        """Return (text, show_background). The background stays visible across short gaps."""
        while self.index < len(self.subtitles):
            currentSub = self.subtitles[self.index]
            if currentTime < currentSub.end.total_seconds():
                break
            self.index += 1

        if self.index >= len(self.subtitles):
            return "", False

        currentSub = self.subtitles[self.index]
        start = currentSub.start.total_seconds()
        end = currentSub.end.total_seconds()

        if start <= currentTime < end:
            return currentSub.content, True

        if currentTime < start and self.index > 0:
            prevEnd = self.subtitles[self.index - 1].end.total_seconds()
            gapDuration = start - prevEnd
            isShortGap = prevEnd <= currentTime and gapDuration <= SUBTITLE_BG_GAP
            return "", isShortGap

        return "", False


class ChunkMarkerHandler:  # Chunk onset/offset events from the chunk file (1st column = concept, 4th column = end time). Never pauses the video.
    def __init__(self, chunk_file_path: Path):
        # Excel file (.xlsx, needs openpyxl); CSV still works if --chunk points to a .csv
        if chunk_file_path.suffix.lower() == ".csv":
            df = pd.read_csv(chunk_file_path)
        else:
            df = pd.read_excel(chunk_file_path, header=0)
        # Only numbered knowledge points ("1. 被綁架", "13. 猜錯機制") are chunks.
        # Skipped: the pause row ("*提問與暫停"), the end-credit row ("X 片尾"),
        # and any numbered row without an end time (e.g. the last concept).
        is_numbered = df.iloc[:, 0].astype(str).str.match(r"^\s*\d+\.")
        end_times = df.iloc[:, 3]
        keep = is_numbered & end_times.notna()
        ends = [self.convert_seconds(i) - CHUNK_TIME_SHIFT for i in end_times[keep]]
        if CHUNK_START_COL is None:
            starts = [0.0] + ends[:-1]
        else:
            starts = []
            for k, v in enumerate(df.iloc[:, CHUNK_START_COL][keep]):
                if pd.isna(v):
                    fallback = ends[k - 1] if k > 0 else 0.0
                    print(f"[WARN] chunk {k + 1}: no start time in column {CHUNK_START_COL + 1}, using {fallback:.3f}")
                    starts.append(fallback)
                else:
                    starts.append(self.convert_seconds(v) - CHUNK_TIME_SHIFT)
        for k, (st, en) in enumerate(zip(starts, ends), start=1):
            if en <= st:
                print(f"[WARN] chunk {k}: end ({en:.3f}) is not after start ({st:.3f})")
        print([(round(st, 3), round(en, 3)) for st, en in zip(starts, ends)])
        # (time, order, kind, chunk number): at the same time an offset comes before the next onset
        events = []
        for k, (st, en) in enumerate(zip(starts, ends), start=1):
            events.append((st, 1, "onset", k))
            events.append((en, 0, "offset", k))
        self.events = [(t, kind, k) for t, _, kind, k in sorted(events)]
        self.index = 0

    def pop_due(self, currentTime):
        """Return [(kind, chunk number)] for every onset/offset whose time has been reached.
        kind is "onset" or "offset"; chunk numbers are 1-based."""
        due = []
        while self.index < len(self.events) and currentTime >= self.events[self.index][0]:
            _, kind, k = self.events[self.index]
            due.append((kind, k))
            self.index += 1
        return due

    def convert_seconds(self, value):
        # Accepts Excel time cells (datetime.time or fraction of a day), or text like "01:23" / "1:02:03".
        if isinstance(value, dtime):
            return value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1e6
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if 0 <= value < 1:
                return value * 86400  # Excel stores times as a fraction of a day
            raise ValueError(f"Unexpected chunk time value: {value!r}")
        parts = str(value).strip().split(":")
        nums = [float(p) for p in parts]
        if len(nums) == 2:
            minutes, seconds = nums
            return int(minutes) * 60 + seconds
        if len(nums) == 3:
            hours, minutes, seconds = nums
            return int(hours) * 3600 + int(minutes) * 60 + seconds
        raise ValueError(f"Unexpected chunk time value: {value!r}")


# LSL Stream to record the chunk and video status events
class MarkerSender:
    """Same marker format as display_lsl: one string "label|value=..|key=val", 1-channel string stream,
    stamped with local_clock() (same time base as EEG / Tobii in LabRecorder)."""

    def __init__(self, name="VideoExpMarker"):
        info = StreamInfo(name=name, type='Markers', channel_count=1, nominal_srate=0,
                          channel_format='string', source_id='bcigame_marker')
        self.outlet = StreamOutlet(info)

    def push(self, label, value=None, verbose=True, **fields):
        parts = [str(label)]
        if value is not None:
            parts.append(f"value={value}")
        for key, field_value in fields.items():
            parts.append(f"{key}={field_value}")
        marker = "|".join(parts)
        self.outlet.push_sample([marker], local_clock())
        if verbose:
            print(f"[MARKER] {marker}")

    def push_on_flip(self, win, label, value=None, **fields):
        """Send the marker right after the next win.flip(), i.e. when the frame is really on screen."""
        win.callOnFlip(self.push, label, value, **fields)

    def send_trigger(self, msg, verbose=True):  # kept so older calls keep working
        self.push(msg, verbose=verbose)


def draw_video_frame(video):
    try:
        video.draw()  # Draw the next frame of the video
        return True
    except RuntimeError as exc:
        if "Failed to return a frame after multiple attempts" in str(exc):
            return False
        raise


def run_timed_screen(win, kb, sender, gaze_recorder, mirror, stims, duration, label, phase):
    """Show `stims` (plain black screen if empty) for `duration` seconds.
    Sends {label}_onset on the first flip and {label}_offset when the time is up.
    ESC sends escape_pressed and returns False."""

    def show_frame():
        for stim in stims:
            stim.draw()
        if mirror is not None:
            mirror.send_stims(win, stims)
        win.flip()

    kb.clearEvents()
    gaze_recorder.set_state(phase=phase, chunk_index="", chunk_text="")
    sender.push_on_flip(win, f"{label}_onset")
    show_frame()
    clock = core.Clock()  # starts right after the onset flip
    while clock.getTime() < duration:
        show_frame()
        if kb.getKeys(keyList=["escape"], waitRelease=False, clear=True):
            sender.push("escape_pressed", phase=phase)
            return False
    sender.push(f"{label}_offset")
    return True


def set_subtitle(win, subtitle_text, text: str):
    """Update subtitle text and shrink it if it would overflow the subtitle box."""
    subtitle_text.text = text or ""
    subtitle_text.height = SUB_FONT_HEIGHT_CM
    for _ in range(6):
        bbox = getattr(subtitle_text, "boundingBox", (0, 0))
        width_cm = float(pix2cm(bbox[0], win.monitor))
        height_cm = float(pix2cm(bbox[1], win.monitor))
        if width_cm <= SUB_WIDTH_CM * 0.95 and height_cm <= SUB_BG_HEIGHT_CM - 0.4:
            break
        subtitle_text.height *= 0.9


def resolve_video_id(raw) -> int:
    """Accept 1, 01, 001, V1, V001 etc. and return the number (1-83)."""
    digits = str(raw).strip().upper().lstrip("V")
    if not digits.isdigit() or not (VIDEO_ID_MIN <= int(digits) <= VIDEO_ID_MAX):
        raise SystemExit(f"影片編號無效：{raw}（應為 {VIDEO_ID_MIN}–{VIDEO_ID_MAX}）")
    return int(digits)


def resolve_paths(args):
    """Work out file paths from --id / --riddle, --sub-lang, and any explicit overrides."""
    vid = f"V{args.riddle:03d}"
    paths = {
        "video": args.video or Path(f"video/{vid}.mp4"),
        "chunk": args.chunk or Path(f"chunk/{vid}.xlsx"),
    }
    if args.srt is not None:
        paths["subtitle"] = args.srt
    elif args.sub_lang is not None:
        paths["subtitle"] = Path(f"subtitles/{vid}.{args.sub_lang}.srt")
    return paths


def check_files(paths):
    missing = [str(p) for p in paths.values() if not p.exists()]
    if missing:
        raise FileNotFoundError("Missing experiment files: " + ", ".join(missing))


def parse_args():
    parser = argparse.ArgumentParser(description="Video stimulus (plays straight through, chunk-end markers only) with optional Tobii gaze / EEG recording via LSL.")
    parser.add_argument("--participant", default=None, help="Participant name. If omitted, the script asks.")
    parser.add_argument("--eyetracker", choices=["off", "tobii"], default="off", help="Tobii gaze recording. Default: off.")
    parser.add_argument("--eeg", choices=["off", "lsl"], default="off", help="EEG recording through LSL. Default: off.")
    parser.add_argument("--eeg-stream-name", default=None, help="Exact LSL EEG stream name, e.g. ActiView.")
    parser.add_argument("--eeg-stream-type", default="EEG", help="LSL stream type used when no name is given. Default: EEG.")
    parser.add_argument("--id", default=None,
                        help="Video ID, e.g. 1, 01, 001 or V001 (1-83). Sets video, subtitle and chunk files automatically.")
    parser.add_argument("--riddle", type=int, default=RIDDLE_NO, help="Video number (same as --id, numeric only). Default: 2.")
    parser.add_argument("--sub-lang", default=SUB_LANG, help="Subtitle language: zh-TW, en, or off. Default: zh-TW.")
    parser.add_argument("--video", type=Path, default=None, help="Override video file path.")
    parser.add_argument("--srt", type=Path, default=None, help="Override subtitle file path.")
    parser.add_argument("--chunk", type=Path, default=None, help="Override chunk Excel file path.")
    parser.add_argument("--participant-screen", type=int, default=PARTICIPANT_SCREEN, help="Screen index for the participant display. Default: 1.")
    parser.add_argument("--mirror-screen", nargs="?", type=int, const=-1, default=None, metavar="INDEX",
                        help="Show the stimuli and gaze trajectory on the experimenter screen. Optionally give its index; default: automatic.")
    parser.add_argument("--no-baseline", action="store_true", help="Skip the eyes-open / eyes-closed baseline before the video.")
    parser.add_argument("--no-wait", action="store_true", help="Skip the 'press ENTER' pause for LabRecorder setup.")
    args = parser.parse_args()
    if args.id is not None:  # --id overrides --riddle, --video, --srt and --chunk
        args.riddle = resolve_video_id(args.id)
        args.video = None
        args.srt = None
        args.chunk = None
    if args.sub_lang is not None and args.sub_lang.lower() in ("off", "none", ""):
        args.sub_lang = None
    return args


def main():
    args = parse_args()
    riddle_no = args.riddle
    paths = resolve_paths(args)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    session_id = datetime.now().strftime("%Y%m%d%H%M%S")
    participant_name = args.participant or input("Participant name: ").strip() or "participant"
    safe_name = "".join(ch if ch not in '<>:"/\\|?*' else "_" for ch in participant_name)

    # From here on everything printed to the terminal is also saved to this txt file
    log_path = RESULTS_DIR / f"{safe_name}_log_{session_id}.txt"
    setup_logging(log_path)
    print("Log file:", log_path.resolve())
    print("Command line:", " ".join(sys.argv))
    print("Args:", vars(args))

    check_files({k: v for k, v in paths.items()})

    print("Current working directory:", Path.cwd())
    print("Results directory:", RESULTS_DIR.resolve())

    if not args.no_wait:
        input("Set up LabRecorder (and check the eye tracker), then press ENTER to continue...")
    gaze_path = RESULTS_DIR / f"{safe_name}_gaze_{session_id}.csv"
    eeg_path = RESULTS_DIR / f"{safe_name}_eeg_{session_id}.csv"

    gaze_recorder = create_gaze_recorder(args.eyetracker, session_id=session_id, participant_name=participant_name)
    eeg_recorder = create_eeg_recorder(args.eeg, stream_name=args.eeg_stream_name, stream_type=args.eeg_stream_type)
    sender = MarkerSender()

    mirror = None
    if args.mirror_screen is not None:
        mirror_index, _ = resolve_screen_configuration(
            participant_screen_index=args.participant_screen,
            requested_mirror_index=args.mirror_screen,
        )
        mirror_params = SimpleNamespace(screen_index=args.participant_screen, screen_width_cm=SCREEN_WIDTH_CM)
        mirror = MirrorController(gaze_recorder, mirror_params, observer_screen_index=mirror_index)

    ### PsychoPy Objects
    monitor = monitors.Monitor("videostim_monitor", width=SCREEN_WIDTH_CM, distance=VIEWING_DISTANCE_CM)
    monitor.setSizePix(SCREEN_PX)

    win = visual.Window(
        size=SCREEN_PX,
        fullscr=FULLSCR,
        screen=args.participant_screen,
        units="cm",
        color="black",
        monitor=monitor,
    )

    video = VlcMovieStim(
        win=win,
        filename=str(paths["video"]),
        units="cm",
        size=VIDEO_SIZE_CM,
        pos=(VALID_CENTER_X, VALID_CENTER_Y),
        loop=False,
        autoStart=False,
    )

    subtitle_bg = visual.Rect(
        win=win,
        units="cm",
        width=SUB_WIDTH_CM,
        height=SUB_BG_HEIGHT_CM,
        pos=(VALID_CENTER_X, SUB_CENTER_Y),
        fillColor="black",
        lineColor=None,
        opacity=0.70,
        autoLog=False,
    )

    subtitle_text = visual.TextStim(
        win=win,
        units="cm",
        text="",
        pos=(VALID_CENTER_X, SUB_CENTER_Y),
        height=SUB_FONT_HEIGHT_CM,
        wrapWidth=SUB_WIDTH_CM * 0.95,
        color="white",
    )

    # Start screen shown before the video
    start_text = visual.TextStim(
        win=win,
        units="cm",
        text=f"[Video {riddle_no}]\n\nPress Space Key to Start",
        pos=(0, 0),
        height=1.2,
        wrapWidth=SCREEN_WIDTH_CM * 0.8,
        color="white",
    )

    # Fixation cross for the baseline (black screen + "+")
    fixation = visual.TextStim(
        win=win,
        units="cm",
        text="+",
        pos=FIXATION_POS_CM,
        height=FIXATION_HEIGHT_CM,
        color="white",
    )

    cue_text = visual.TextStim(
        win=win,
        units="cm",
        text="",
        pos=(0, 0),
        height=CUE_HEIGHT_CM,
        wrapWidth=SCREEN_WIDTH_CM * 0.8,
        color="white",
    )

    kb = keyboard.Keyboard()
    kb.clearEvents()

    is_running = True

    try:
        gaze_recorder.start()
        eeg_recorder.start()
        if mirror is not None:
            mirror.start()
        gaze_recorder.set_state(phase="start_screen", chunk_index="", chunk_text="")

        core.wait(1)  # wait 1 s for LSL, eyetracker and EEG to start

        videoSub = SrtHandler(paths["subtitle"]) if "subtitle" in paths else None
        chunkHandler = ChunkMarkerHandler(paths["chunk"])
        video_markers = VIDEO_MARKERS.get(riddle_no, [])
        marker_idx = 0
        video_id = f"V{riddle_no:03d}"
        video_started = False

        # Start screen: wait for Space before playing the video
        start_text.draw()
        if mirror is not None:
            mirror.send_stims(win, [start_text])
        win.flip()
        sender.send_trigger(f"Instruction_{riddle_no}")
        switch_to_english_input_method()
        start_keys = event.waitKeys(keyList=["space", "escape"])
        if "escape" in start_keys:
            sender.push("escape_pressed", phase="start_screen")
            is_running = False

        if is_running and not args.no_baseline:
            # 30 s eyes open (+) -> 5 s cue text -> 30 s eyes closed (+), each with onset/offset markers
            cue_text.text = CUE_EYES_CLOSED_TEXT
            baseline_steps = [
                ([fixation], BASELINE_FIXATION_S, "openeye", "baseline_open_eyes"),
                ([cue_text], BASELINE_CUE_S, "restcue", "baseline_cue"),
                ([fixation] if EYES_CLOSED_SHOW_FIXATION else [], BASELINE_EYES_CLOSED_S, "closeeye", "baseline_closed_eyes"),
            ]
            for stims, dur, label, phase in baseline_steps:
                is_running = run_timed_screen(win, kb, sender, gaze_recorder, mirror, stims, dur, label, phase)
                if not is_running:
                    break

        if is_running:
            gaze_recorder.set_state(phase="video_playing", chunk_index="", chunk_text="")
            video.play()

        while is_running and not video.isFinished:
            frame_stims = []
            if not draw_video_frame(video):
                break
            currentTime = video.frameTime
            gaze_recorder.set_state(video_time=f"{currentTime:.3f}")

            ### Video onset: sent on the flip that shows the first video frame
            if not video_started:
                sender.push_on_flip(win, "video_onset", video=video_id)
                video_started = True

            ### Fixed-time markers (e.g. fixation / eyes-closed segments)
            while marker_idx < len(video_markers) and currentTime >= video_markers[marker_idx][0]:
                sender.send_trigger(f"Video{riddle_no}_{video_markers[marker_idx][1]}")
                marker_idx += 1

            ### Chunk onset / offset markers (video keeps playing; sent on the flip that shows that frame)
            for kind, chunk_no in chunkHandler.pop_due(currentTime):
                sender.push_on_flip(win, f"chunk_{kind}", chunk=chunk_no)
                gaze_recorder.set_state(chunk_index=chunk_no if kind == "onset" else "", chunk_text="")

            ### Handling Subtitles Display
            if videoSub is not None:
                sub_text, show_subtitle_bg = videoSub.getSubtitleState(currentTime)
                if sub_text != subtitle_text.text:
                    set_subtitle(win, subtitle_text, sub_text)
                if show_subtitle_bg:
                    subtitle_bg.draw()
                    frame_stims.append(subtitle_bg)
                if sub_text:
                    subtitle_text.draw()
                    frame_stims.append(subtitle_text)

            if mirror is not None:
                mirror.send_stims(win, frame_stims)
            win.flip()

            keys = kb.getKeys(keyList=["escape"], waitRelease=False, clear=True)
            if keys:
                sender.push("escape_pressed", phase="video")
                is_running = False

        if is_running:
            sender.push("video_offset", video=video_id)

    finally:
        if mirror is not None:
            mirror.close()
        gaze_recorder.stop()
        eeg_recorder.stop()
        print("Saving gaze to:", gaze_path.resolve())
        gaze_recorder.save_csv(gaze_path)
        print("After save, gaze exists:", gaze_path.exists())

        print("Saving EEG to:", eeg_path.resolve())
        eeg_recorder.save_csv(eeg_path)
        print("After save, EEG exists:", eeg_path.exists())

        win.close()
        print("Session finished. Log saved to:", log_path.resolve())


if __name__ == "__main__":
    main()