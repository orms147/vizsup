---
name: run-stage
description: Run or debug a single vizsup pipeline stage (download, ASR, OCR, translate, TTS, assemble) on a sample clip. Use when the user wants to test/iterate on one stage in isolation rather than the whole pipeline.
---

# Run / debug one pipeline stage

Each stage is a function over a `Job` (a working directory). Run them in isolation from a Python REPL or a scratch script.

## Setup
```bash
cd backend
pip install -e .            # core; add extras as needed: pip install -e ".[asr,ocr,translate]"
```

## Make a Job
```python
from pathlib import Path
from app.models import Job
job = Job(id="scratch", work_dir=Path("../storage/scratch"), url="<url>")
```

## Run a stage
- **download**: `from app.pipeline import download; download.download(job)`
- **ASR** (P2 stub): `from app.pipeline import asr; asr.transcribe(job, provider="whisperx_funasr")` → writes `job.cn_srt`
- **translate**: `from app.pipeline import translate; translate.translate(job, draft="deepseek", refine="claude")` → writes `job.vi_srt`
- **TTS + assemble**: `from app.pipeline import tts, assemble; segs = tts.synthesize(job, provider="edge"); assemble.assemble(job, segs)`

## Whole cheap path (CLI)
```bash
python -m app.cli run <url> --srt sample.cn.srt --translator passthrough
python -m app.cli render --workdir ../storage/<id>
```

## Debugging
- ffmpeg/whisper/torch/CUDA errors → hand off to the `pipeline-debugger` subagent.
- Inspect intermediate files in the job's `work_dir`: `source.mp4`, `cn.srt`, `vi.srt`, `subs.ass`, `tts/`, `output.mp4`.
- Check FFmpeg has rubberband: `ffmpeg -filters | findstr rubberband`.
- 8GB VRAM: run heavy models sequentially; don't load WhisperX + FunASR + Demucs at once.
