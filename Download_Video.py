import yt_dlp

url = input("Video URL: ")
name = input("Output Filename: ")

ydl_opts = {
    "noplaylist": True,
    "format": "bv*[height<=720]+ba/b[height<=720]", #best video + best audio (up to 720p)
    "merge_output_format": "mp4",
    "writesubtitles": True,
    "subtitleslangs": ["en.*"],
    "subtitlesformat": "srt",
    "outtmpl": {
        "default": f"video/{name}.%(ext)s",
        "subtitle": f"video/subtitles/{name}.%(ext)s",
    },
}

with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    ydl.download([url])