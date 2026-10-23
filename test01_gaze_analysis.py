import csv
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

SEG_DIR = "/root/.claude/uploads/2e41b81a-0c58-5152-b984-ee0445c84b46/"
GAZE_DIR = "/mnt/user-data/attachments/2e41b81a-0c58-5152-b984-ee0445c84b46/"
SEG = {"V037": "064ccbc3-V037.csv", "V011": "08d4249f-V011.csv",
       "V044": "84cca124-V044.csv", "V032": "26f75257-V032.csv"}
GAZE = {"V037": "5bf53d0b-test01_gaze_20261020142134.csv",
        "V011": "b1e66097-test01_gaze_20261020143546.csv",
        "V044": "d66cf8b7-test01_gaze_20261020144209.csv",
        "V032": "41d2dcf7-test01_gaze_20261020145459.csv"}
RATINGS = {"V037": [1,1,2,1,1,1,1,1,4,3,4,4,4], "V011": [1,1,1,1,1,1,1,2,1,1,4,4],
           "V044": [1,1,1,1,1,1,5,4,5,1,4,4,2,5], "V032": [1,1,1,1,1,1,2,2,2,4,2,2]}
MARKERS = {"V037": [3, 9]}  # teacher's pay-attention markers

def tsec(s):
    s = s.strip()
    if not s:
        return None
    m, sec = s.split(":")
    return int(m) * 60 + int(sec)

def load_segments(path):
    segs, pause_done = [], False
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.reader(f); next(rd)
        for row in rd:
            if not row or not row[0].strip():
                continue
            name = row[0].strip()
            if name.startswith("X"):
                break
            if name.startswith("*"):
                continue
            segs.append(dict(name=name, start=tsec(row[-2]), end=tsec(row[-1])))
    return segs

def gaze_frame(path):
    df = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    p = df[df["phase"] == "video_playing"].copy()
    for c in ["left_x", "left_y", "right_x", "right_y", "video_time", "pc_time", "left_valid", "right_valid"]:
        p[c] = pd.to_numeric(p[c], errors="coerce")
    lv = (p.left_valid == 1) & p.left_x.between(0, 1) & p.left_y.between(0, 1)
    rv = (p.right_valid == 1) & p.right_x.between(0, 1) & p.right_y.between(0, 1)
    gx = np.where(lv & rv, (p.left_x + p.right_x) / 2, np.where(lv, p.left_x, np.where(rv, p.right_x, np.nan)))
    gy = np.where(lv & rv, (p.left_y + p.right_y) / 2, np.where(lv, p.left_y, np.where(rv, p.right_y, np.nan)))
    p["gx"], p["gy"] = gx, gy
    dt = p.pc_time.diff()
    dist = np.hypot(p.gx.diff(), p.gy.diff())
    speed = np.where((dt > 0) & (dt < 0.05), dist / dt, np.nan)
    p["speed"] = np.clip(speed, 0, 5)
    return p.reset_index(drop=True)

def seg_metrics(p, segs, n_rating):
    vmax = p.video_time.max()
    rows = []
    for i, s in enumerate(segs):
        end = s["end"] if s["end"] is not None else vmax
        w = p[(p.video_time >= s["start"]) & (p.video_time < end)]
        valid = w.gx.notna()
        disp = np.sqrt(np.nanvar(w.gx) + np.nanvar(w.gy)) if valid.sum() > 1 else np.nan
        rows.append(dict(seg=i + 1, name=s["name"], start=s["start"], end=end,
                         n=len(w), valid_rate=valid.mean() if len(w) else np.nan,
                         dispersion=disp, speed=np.nanmean(w.speed) if w.speed.notna().any() else np.nan))
    return pd.DataFrame(rows)

cjk = [f.name for f in font_manager.fontManager.ttflist if "CJK" in f.name or "Noto Sans TC" in f.name]
plt.rcParams["font.family"] = ["DejaVu Sans"] + cjk[:1]
plt.rcParams["axes.unicode_minus"] = False

vids = list(SEG)
allrows = []
gazes = {}
for v in vids:
    segs = load_segments(SEG_DIR + SEG[v])
    p = gaze_frame(GAZE_DIR + GAZE[v])
    gazes[v] = p
    m = seg_metrics(p, segs, len(RATINGS[v]))
    m["difficulty"] = [RATINGS[v][i] if i < len(RATINGS[v]) else np.nan for i in range(len(m))]
    m.insert(0, "video", v)
    allrows.append(m)
    print("==", v, "segments", len(segs), "ratings", len(RATINGS[v]))
    print(m[["seg", "start", "end", "n", "valid_rate", "dispersion", "speed", "difficulty"]].round(3).to_string(index=False))

res = pd.concat(allrows, ignore_index=True)
res.to_csv("/tmp/claude-0/-home-claude/2e41b81a-0c58-5152-b984-ee0445c84b46/scratchpad/gaze_per_segment.csv", index=False)

# V037: marker segments vs rest
m37 = res[res.video == "V037"].set_index("seg")
mk = m37.loc[[3, 9]]
rest = m37.drop([3, 9])
print("V037 markers vs rest (dispersion / speed / valid):")
print(" markers", mk[["dispersion", "speed", "valid_rate"]].mean().round(3).to_dict())
print(" rest   ", rest[["dispersion", "speed", "valid_rate"]].mean().round(3).to_dict())

# Figure 1: heatmaps
fig, axes = plt.subplots(2, 2, figsize=(10, 8), dpi=150)
for ax, v in zip(axes.flat, vids):
    p = gazes[v].dropna(subset=["gx", "gy"])
    h, xe, ye = np.histogram2d(p.gx, p.gy, bins=60, range=[[0, 1], [0, 1]])
    im = ax.imshow(h.T, origin="upper", extent=[0, 1, 1, 0], cmap="hot", aspect="auto")
    ax.set_title(f"{v}（n={len(p)}）")
    ax.set_xlabel("x（正規化）"); ax.set_ylabel("y（正規化）")
fig.suptitle("test01 — 注視熱圖（全部播放期間，y 軸由上往下）", fontsize=12)
fig.tight_layout(rect=[0, 0, 1, 0.96])
fig.savefig("/mnt/user-data/outputs/test01_gaze_heatmap.png")

# Figure 2: per-video difficulty vs gaze dispersion by segment
fig, axes = plt.subplots(4, 1, figsize=(11, 15), dpi=150)
for ax, v in zip(axes, vids):
    d = res[res.video == v]
    x = d.seg.values
    ax.bar(x, d.dispersion.values, width=0.6, color="#A6A6A6", alpha=0.8, label="注視分散度（灰條）")
    ax.set_ylabel("注視分散度")
    ax.set_ylim(0, max(0.3, np.nanmax(d.dispersion.values) * 1.25))
    ax2 = ax.twinx()
    ax2.plot(x, d.difficulty.values, marker="o", color="#4472C4", lw=2, label="自評難度（藍線）")
    ax2.set_ylim(0.5, 5.5); ax2.set_yticks(range(1, 6)); ax2.set_ylabel("自評難度（1–5）")
    for mk in MARKERS.get(v, []):
        ax.axvline(mk, color="#C00000", ls="--", lw=1.4)
        ax.text(mk + 0.1, ax.get_ylim()[1] * 0.95, f"段落 {mk}", color="#C00000", fontsize=9)
    ax.set_title(v); ax.set_xlabel("段落序號")
    ax.set_xticks(x); ax.grid(alpha=0.3, axis="y")
    ax.spines[["top"]].set_visible(False)
fig.suptitle("test01 — 各段自評難度與注視分散度（紅虛線為老師指定的 pay-attention 標記）", fontsize=11)
fig.tight_layout(rect=[0, 0, 1, 0.97])
fig.savefig("/mnt/user-data/outputs/test01_gaze_vs_difficulty.png")

res.to_excel("/mnt/user-data/outputs/test01_gaze_per_segment.xlsx", index=False)
print("saved figures and xlsx")
