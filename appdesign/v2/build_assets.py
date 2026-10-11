"""Build design fixtures from existing local project files. No AI calls or secrets."""
from pathlib import Path
import array
import json
import math
import re
import subprocess
import wave

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "assets"
JOB = ROOT / "storage" / "2dc712c162d7"
FFMPEG = Path(r"C:\Users\Admin\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe")


def read_srt(path):
    text = path.read_text(encoding="utf-8-sig").replace("\r", "")
    rows = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.splitlines()
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        def seconds(s):
            h, m, sec = s.strip().replace(",", ".").split(":")
            return int(h) * 3600 + int(m) * 60 + float(sec)
        start, end = lines[1].split("-->")
        rows.append({"start": seconds(start), "end": seconds(end), "text": "\n".join(lines[2:])})
    return rows


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    vi, zh = read_srt(JOB / "vi.srt"), read_srt(JOB / "cn.srt")
    cues = [dict(id=i+1, start=v["start"], end=v["end"], vi=v["text"], zh=zh[i]["text"] if i < len(zh) else "", gain=0, mute=False, locked=False) for i, v in enumerate(vi)]
    # Demo report fixtures are marked in the handoff; durations/overlap are real.
    for cue in cues:
        cue["ocr"] = cue["id"] in [4, 16, 20]
        cue["term"] = cue["id"] == 27
    meta = json.loads((JOB / "metadata.json").read_text(encoding="utf-8"))
    context = json.loads((JOB / "context_brief.json").read_text(encoding="utf-8"))["brief"]
    with wave.open(str(JOB / "source.wav")) as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        if width != 2:
            raise RuntimeError("Expected PCM16 fixture")
        samples = array.array("h", w.readframes(w.getnframes()))
    mono = samples[::channels]
    count = 3600
    chunk = max(1, len(mono) // count)
    rms = [math.sqrt(sum(s*s for s in mono[i:i+chunk]) / max(1, len(mono[i:i+chunk]))) for i in range(0, len(mono), chunk)]
    peak = max(rms) or 1
    waveform = [round(min(1, (v/peak)**0.6), 3) for v in rms[:count]]
    payload = {"title": "Trà ấm trước chuyến đi", "originalTitle": meta["title"], "duration": meta["duration"], "width": 720, "height": 1566, "cues": cues, "waveform": waveform, "context": context, "fixtureProject": "2dc712c162d7", "sourceLocal": "../../storage/2dc712c162d7/source.mp4"}
    (OUT / "project-data.js").write_text("window.VIZSUP_DATA = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
    for name, timestamp, source in [("tea", 39.45, JOB / "source.mp4"), ("school", 21.4, ROOT / "storage" / "bb15cbcac86c" / "source.mp4")]:
        subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-ss", str(timestamp), "-i", str(source), "-frames:v", "1", "-vf", "scale=600:-2", "-q:v", "3", str(OUT / f"{name}.jpg")], check=True)
    print(f"Built {len(cues)} real cues, {len(waveform)} audio samples and 2 source frames.")


if __name__ == "__main__":
    main()
