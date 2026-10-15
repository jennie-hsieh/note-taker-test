# VideoStim: Video Experiment with Eye-Tracking and EEG Recording

Plays a video with subtitles in PsychoPy, pauses it at times defined in a chunk file, and asks the participant to press F or J to report confusion. It can record Tobii eye-tracking data and EEG (via LSL), and includes a gaze replay tool.

## Files

| File | Purpose |
|---|---|
| `VideoStim.py` | Main script: video playback, subtitles, pauses and key responses, eye-tracking and EEG recording |
| `eyetracker.py` | Tobii eye-tracking recorder |
| `biosemi_recorder.py` | EEG recorder (receives an ActiView stream via LSL) |
| `replay_video_gaze.py` | Gaze replay: overlays gaze points, subtitles and the valid-area frame on the video |
| `requirements.txt` | Python package list |
| `Download_Video.py` | Video download helper (if available) |

## Folder Structure

```
Note taker\
├── VideoStim.py
├── eyetracker.py
├── biosemi_recorder.py
├── replay_video_gaze.py
├── requirements.txt
├── video\
│   └── Riddle_2.mp4
├── subtitles\
│   ├── Riddle_2.en.srt
│   └── Riddle_2.zh.srt      (Chinese subtitles, optional)
├── chunk\
│   └── Riddle_2.csv
└── results\                  (created automatically)
```

## Setup

1. **Python**: a conda environment is recommended (this project uses `NOTA`).
   ```
   conda activate NOTA
   ```
2. **Packages**:
   ```
   python -m pip install -r requirements.txt
   ```
3. **VLC Media Player (64-bit)**: required by `VlcMovieStim`.
   - To avoid noise when playback resumes, open VLC preferences and set the output module to "Windows Multimedia Device Output" (not "auto").
4. **Tobii eye tracker (optional, needed for `--eyetracker tobii`)**:
   - Install Tobii Pro Eye Tracker Manager and complete calibration. Set the display area to the full screen and use a viewing distance of about 60 cm.
   - Check that this command lists the eye tracker:
     `python -c "import tobii_research as tr; print(tr.find_all_eyetrackers())"`
5. **ffmpeg (needed for downloading videos)**: `winget install ffmpeg`, then reopen the terminal.
6. **yt-dlp (for downloading videos)**:
   ```
   python -m pip install -U "yt-dlp[default]"
   ```

## Preparing Materials

### Video and Subtitles

Download with yt-dlp (example: `BSF9s0gbJ2M`):

```
yt-dlp --no-playlist -f "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]" --merge-output-format mp4 -o "video/Riddle_2.%(ext)s" "https://www.youtube.com/watch?v=BSF9s0gbJ2M"
yt-dlp --no-playlist --skip-download --write-subs --write-auto-subs --sub-langs en --convert-subs srt -o "subtitles/Riddle_2" "https://www.youtube.com/watch?v=BSF9s0gbJ2M"
```

- Keep the video and subtitle files in separate folders so the subtitles are not embedded in the video.
- Subtitles must be in **srt format** (timestamps like `00:00:01,000 --> 00:00:03,500`), not vtt.
- If YouTube blocks the download, add `--cookies-from-browser chrome`, or download the subtitles manually and rename them.

### Pause Points (Chunk File)

`chunk\Riddle_2.csv` must have four columns. The script reads the **fourth column, `time`**:

```
id,name,target,time
1,chunk1,,0:30
2,chunk2,,1:05
```

- Times use the format `minutes:seconds` and refer to the video playback time.
- List them in ascending order.
- If the file contains only the header row, the video plays through without pausing.

## Running the Experiment

```
python VideoStim.py --participant <name> [options]
```

| Option | Description | Default |
|---|---|---|
| `--participant` | Participant name, used in file names | Asked at startup |
| `--eyetracker` | `off` or `tobii` | `off` |
| `--eeg` | `off` or `lsl` | `off` |
| `--eeg-stream-name` | LSL EEG stream name (e.g. `ActiView`) | none |
| `--eeg-stream-type` | Stream type used when no name is given | `EEG` |
| `--video` | Video file path | `video\Riddle_2.mp4` |
| `--srt` | Subtitle file path | `subtitles\Riddle_2.en.srt` |
| `--chunk` | Pause-point CSV path | `chunk\Riddle_2.csv` |

Examples:

```
python VideoStim.py --participant test001 --eyetracker tobii
python VideoStim.py --participant test001 --eyetracker tobii --srt subtitles\Riddle_2.zh.srt
python VideoStim.py --participant test001 --eyetracker tobii --eeg lsl --eeg-stream-name ActiView
```

### Procedure

1. The video plays full screen, with subtitles shown at the bottom of the valid area.
2. At each chunk time, the video pauses and a question appears: "Do you find the content just now confusing?"
3. The participant presses **F** (confused) or **J** (not confused).
4. After `pauseTime` (default 3 seconds), the video resumes automatically.
5. Press **ESC** to end. The data are saved.

LSL markers (`VideoPaused`, `Confused`, `NotConfused`, `VideoResume`) are sent throughout.

## Output Files

| File | Contents |
|---|---|
| `results\<name>_gaze_<time>.csv` | Eye-tracking data: `pc_time`, `video_time`, `phase`, left and right eye coordinates and validity flags |
| `results\<name>_eeg_<time>.csv` | EEG data (only with `--eeg lsl`): `pc_time`, `lsl_time`, one column per channel |

The gaze CSV only contains data when `--eyetracker tobii` is used.

## Gaze Replay

```
python replay_video_gaze.py results\<name>_gaze_<time>.csv
```

You can add `--video` and `--srt` to specify the materials.

| Key | Action |
|---|---|
| Space | Pause / resume |
| Left / Right | Jump back / forward 2 seconds |
| V | Show / hide the valid-area frame |
| I | Show / hide the info bar |
| ESC | Quit |

Gaze point colors: red means inside the valid area, yellow means outside.

**Note**: replay requires a gaze CSV that contains the `video_time` column, which is written by the current `VideoStim.py` and `eyetracker.py`. Older CSVs will raise an error.

## Screen and Valid-Area Settings

The constants below are at the top of both `VideoStim.py` and `replay_video_gaze.py`. **The two files must match.**

| Constant | Current value | Description |
|---|---|---|
| `SCREEN_PX` | `(1680, 1050)` | Actual screen resolution; must match the system setting |
| `SCREEN_WIDTH_CM` / `SCREEN_HEIGHT_CM` | 47.5 / 30.0 | Physical screen size |
| `VIEWING_DISTANCE_CM` | 60.0 | Viewing distance |
| `EYE_VALID_LEFT_CM` / `EYE_VALID_TOP_CM` | 4.0 / 6.5 | Top-left corner of the valid area, measured from the screen's top-left corner |
| `EYE_VALID_WIDTH_CM` / `EYE_VALID_HEIGHT_CM` | 37.5 / 18.0 | Size of the valid area |
| `VIDEO_ASPECT` | 16/9 | Video aspect ratio; change if the video differs |
| `pauseTime` (in `PauseHandler`) | 3.0 | Seconds per pause |

**Known measurement issue**: in the current valid-area diagram, the vertical values add up to 6.5 + 18 + 6.5 = 31 cm, which is 1 cm more than the screen height of 30 cm. Please measure to confirm.

## Troubleshooting

| Error or symptom | Cause and fix |
|---|---|
| `ModuleNotFoundError` | Package not installed in the current environment. Run `python -m pip install -r requirements.txt` |
| `video\Riddle_2.mp4 file not readable` | Video file missing, misnamed, incomplete, or VLC is not installed or has a different bit version |
| `subtitles\Riddle_2.en.srt` not found | Subtitles were not downloaded or not converted to srt |
| `No Tobii eye tracker found` | Eye tracker not connected via USB, or Tobii services are not running |
| Warning `screen is actually [...]` | `SCREEN_PX` does not match the actual resolution; update it |
| Video never pauses | `chunk\Riddle_2.csv` has no time values |
| Replay reports an error about `video_time` | Old CSV; record a new session with the current version |

## Notes

- Make sure you have the rights to use the videos and subtitles, especially for research.
- The EEG CSV format is intended for simple testing. For long or multi-channel recordings, use LabRecorder and save as XDF.
- Eye-tracking and EEG CSVs both include `pc_time` and can be aligned with it. LSL marker timestamps should be aligned using the XDF file.
