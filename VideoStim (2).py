import argparse
from pathlib import Path
from datetime import datetime

import pandas as pd
import srt
from psychopy import visual, core, monitors
from psychopy.visual.vlcmoviestim import VlcMovieStim
from psychopy.hardware import keyboard
from psychopy.tools.monitorunittools import pix2cm
from pylsl import StreamInfo, StreamOutlet

from eyetracker import create_gaze_recorder
from biosemi_recorder import create_eeg_recorder


### Settings
# Display: the Tobii gaze coordinates map to the WHOLE screen,
# so the window must be fullscreen at the real display resolution.
SCREEN_PX = (1680, 1050)          # set to your display resolution
SCREEN_WIDTH_CM = 47.5
SCREEN_HEIGHT_CM = 30.0
VIEWING_DISTANCE_CM = 60.0
FULLSCR = True

# Eyetracker valid area (cm, measured from the screen's top-left corner)
EYE_VALID_LEFT_CM = 4.0
EYE_VALID_TOP_CM = 6.5
EYE_VALID_WIDTH_CM = 37.5
EYE_VALID_HEIGHT_CM = 18.0

# Default files (override with --video / --srt / --chunk)
VIDEO_PATH = Path("video/Riddle_2.mp4")
VIDEO_ASPECT = 16 / 9             # width / height of the video file
SRT_FILE_PATH = Path("subtitles/Riddle_2.en.srt")
CHUNK_CSV_PATH = Path("chunk/Riddle_2.csv")

# Pause / confusion rating
PAUSE_TIME = 5                    # seconds allowed for the rating; -1 = no timeout
PAUSE_TIME_SHIFT = 0              # seconds; positive pauses earlier
RATING_KEYS = [str(i) for i in range(1, 10)]   # 1 = no confusion, 9 = extreme confusion

# Subtitles
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
    def __init__(self, srt_file_path: Path, delay=0):
        with srt_file_path.open("r", encoding="utf-8-sig") as f:
            subtitle_text = f.read()
        self.subtitles = list(srt.parse(subtitle_text))
        self.subtitles = list(srt.sort_and_reindex(self.subtitles))  # start index = 1
        self.index = 0
        self.delay = delay

    def getCurrentSub(self, currentTime):
        if self.index < len(self.subtitles):
            currentTime -= self.delay
            currentSub = self.subtitles[self.index]

            start = currentSub.start.total_seconds()
            end = currentSub.end.total_seconds()

            if currentTime < start:
                return ""
            if start <= currentTime < end:
                return currentSub.content
            if currentTime > end:
                self.index += 1
        return ""


class PauseHandler:  # Decide when to pause according to the chunk csv (4th column = time).
    def __init__(self, csv_file_path: Path, pauseTime=PAUSE_TIME):
        self.chunkData = pd.read_csv(csv_file_path)
        self.chunkData = self.chunkData.iloc[:, 3].tolist()  # iloc:[row,column]
        self.chunkData = [self.convert_seconds(i) - PAUSE_TIME_SHIFT for i in self.chunkData]
        print(self.chunkData)
        self.pauseTimer = core.Clock()
        self.shouldPause = False
        self.pauseTime = pauseTime
        self.index = 0

    def getPauseState(self, currentTime):
        if self.index < len(self.chunkData):
            chunkTimeStamp = self.chunkData[self.index]
            if currentTime > chunkTimeStamp:
                if not self.shouldPause:
                    self.shouldPause = True
                    self.pauseTimer.reset()
        return self.shouldPause

    def completePause(self):
        self.shouldPause = False
        self.index += 1

    def getRemainingTimeText(self):
        if self.pauseTime < 0:
            return ""
        return f"({int(max(0, self.pauseTime - self.pauseTimer.getTime()) + 0.999)} s left)"

    def isTimedOut(self):
        if self.pauseTime < 0:
            return False
        return self.pauseTimer.getTime() >= self.pauseTime

    def convert_seconds(self, time_str):
        minutes, seconds = str(time_str).split(":")
        return int(minutes) * 60 + float(seconds)


# LSL Stream to record the rating events and the video status events
class MarkerSender:
    def __init__(self, name="VideoExpMarker"):
        info = StreamInfo(name=name, type='Markers', channel_count=1, nominal_srate=0,
                          channel_format='string', source_id='bcigame_marker')
        self.outlet = StreamOutlet(info)

    def send_trigger(self, msg, verbose=True):
        self.outlet.push_sample([msg])
        if verbose:
            print(f"[MARKER] {msg}")


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


def parse_args():
    parser = argparse.ArgumentParser(description="Video stimulus with confusion rating and optional Tobii gaze / EEG recording via LSL.")
    parser.add_argument("--participant", default=None, help="Participant name. If omitted, the script asks.")
    parser.add_argument("--eyetracker", choices=["off", "tobii"], default="off", help="Tobii gaze recording. Default: off.")
    parser.add_argument("--eeg", choices=["off", "lsl"], default="off", help="EEG recording through LSL. Default: off.")
    parser.add_argument("--eeg-stream-name", default=None, help="Exact LSL EEG stream name, e.g. ActiView.")
    parser.add_argument("--eeg-stream-type", default="EEG", help="LSL stream type used when no name is given. Default: EEG.")
    parser.add_argument("--video", type=Path, default=VIDEO_PATH, help="Video file. Default: video/Riddle_2.mp4")
    parser.add_argument("--srt", type=Path, default=SRT_FILE_PATH, help="Subtitle file. Default: subtitles/Riddle_2.en.srt")
    parser.add_argument("--chunk", type=Path, default=CHUNK_CSV_PATH, help="Chunk/pause CSV. Default: chunk/Riddle_2.csv")
    parser.add_argument("--no-wait", action="store_true", help="Skip the 'press ENTER' pause for LabRecorder setup.")
    return parser.parse_args()


def main():
    args = parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    session_id = datetime.now().strftime("%Y%m%d%H%M%S")
    participant_name = args.participant or input("Participant name: ").strip() or "participant"
    if not args.no_wait:
        input("Set up LabRecorder (and check the eye tracker), then press ENTER to continue...")
    safe_name = "".join(ch if ch not in '<>:"/\\|?*' else "_" for ch in participant_name)
    gaze_path = RESULTS_DIR / f"{safe_name}_gaze_{session_id}.csv"
    eeg_path = RESULTS_DIR / f"{safe_name}_eeg_{session_id}.csv"

    gaze_recorder = create_gaze_recorder(args.eyetracker, session_id=session_id, participant_name=participant_name)
    eeg_recorder = create_eeg_recorder(args.eeg, stream_name=args.eeg_stream_name, stream_type=args.eeg_stream_type)
    sender = MarkerSender()

    ### PsychoPy Objects
    monitor = monitors.Monitor("videostim_monitor", width=SCREEN_WIDTH_CM, distance=VIEWING_DISTANCE_CM)
    monitor.setSizePix(SCREEN_PX)

    win = visual.Window(
        size=SCREEN_PX,
        fullscr=FULLSCR,
        units="cm",
        color="black",
        monitor=monitor,
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

    # Rating box shown while the video is paused
    pause_box = visual.Rect(
        win=win,
        units="cm",
        width=EYE_VALID_WIDTH_CM * 0.9,
        height=9.0,
        pos=(VALID_CENTER_X, VALID_CENTER_Y),
        fillColor="black",
        lineColor="white",
        lineWidth=3,
        opacity=1.0,
        autoLog=False,
    )

    pause_text = visual.TextStim(
        win=win,
        units="cm",
        text="",
        pos=(VALID_CENTER_X, VALID_CENTER_Y),
        height=0.9,
        wrapWidth=EYE_VALID_WIDTH_CM * 0.8,
        color="white",
    )

    kb = keyboard.Keyboard()
    kb.clearEvents()

    is_running = True
    is_playing = True

    try:
        gaze_recorder.start()
        eeg_recorder.start()
        gaze_recorder.set_state(phase="video_playing", chunk_index="", chunk_text="")

        core.wait(1)  # wait 1 s for LSL, eyetracker and EEG to start

        video.play()
        videoSub = SrtHandler(args.srt)
        pauseHandler = PauseHandler(args.chunk)

        while is_running and not video.isFinished:
            video.draw()
            currentTime = video.frameTime
            gaze_recorder.set_state(video_time=f"{currentTime:.3f}")

            ### Handling Pause Behaviors
            pause_state = pauseHandler.getPauseState(currentTime)
            if pause_state:
                if is_playing:  # First enter this condition
                    is_playing = False
                    video.pause()
                    sender.send_trigger("VideoPaused")
                    gaze_recorder.set_state(phase="video_paused", chunk_index=pauseHandler.index, chunk_text="")
                    kb.clock.reset()
                    kb.clearEvents()

                video_keys = kb.getKeys(keyList=RATING_KEYS, waitRelease=False, clear=True)
                pause_finished = False
                if video_keys:  # Only consider the first key
                    rating = video_keys[0].name
                    print(f"keyboard] rating {rating} pressed")
                    sender.send_trigger(f"Rating_{rating}")
                    pause_finished = True
                elif pauseHandler.isTimedOut():
                    print("keyboard] no response")
                    sender.send_trigger("NoResponse")
                    pause_finished = True

                if pause_finished:
                    pauseHandler.completePause()
                    is_playing = True
                    video.play()
                    sender.send_trigger("VideoResume")
                    gaze_recorder.set_state(phase="video_playing", chunk_index="", chunk_text="")
                else:
                    pause_text.text = (
                        "<Video Paused>\n\n"
                        "Rate Your Level of Confusion\n"
                        "1 = No confusion <-> 9 = Extreme Confusion\n\n"
                        f"{pauseHandler.getRemainingTimeText()}"
                    )
                    pause_box.draw()
                    pause_text.draw()

            elif not is_playing:
                is_playing = True
                video.play()
                sender.send_trigger("VideoResume")
                gaze_recorder.set_state(phase="video_playing", chunk_index="", chunk_text="")

            ### Handling Subtitles Display (hidden while the rating box is shown)
            set_subtitle(win, subtitle_text, videoSub.getCurrentSub(currentTime))
            if subtitle_text.text and not pause_state:
                subtitle_bg.draw()
                subtitle_text.draw()

            win.flip()

            keys = kb.getKeys(keyList=["escape"], waitRelease=False, clear=True)
            if keys:
                is_running = False

    finally:
        gaze_recorder.stop()
        eeg_recorder.stop()
        gaze_recorder.save_csv(gaze_path)
        eeg_recorder.save_csv(eeg_path)
        print(f"Gaze data saved to {gaze_path}")
        print(f"EEG data saved to {eeg_path}")
        win.close()


if __name__ == "__main__":
    main()
