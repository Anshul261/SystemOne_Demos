"""Synthesise narration lines with Kokoro-82M (hexgrad/Kokoro-82M, Apache-2.0).

    uv run --no-project --python 3.12 --with kokoro --with soundfile --with <en_core_web_sm wheel> python video/narrate.py lines.json

lines.json maps ids to text. Each line becomes a 24 kHz WAV in video/out/voice/, cached by voice and text,
and the script prints {id: {"path", "seconds"}} as JSON. NARRATOR_VOICE picks the voice (default af_heart).
"""

import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

RATE = 24000
OUT = Path(__file__).parent / "out" / "voice"


def main(spec: str) -> None:
    lines = json.loads(Path(spec).read_text())
    voice = os.getenv("NARRATOR_VOICE", "af_heart")
    OUT.mkdir(parents=True, exist_ok=True)
    pipeline = None
    result = {}
    for key, text in lines.items():
        path = OUT / f"{hashlib.sha1(f'{voice}|{text}'.encode()).hexdigest()[:16]}.wav"
        if not path.exists():
            if pipeline is None:
                from kokoro import KPipeline
                pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
            audio = np.concatenate([chunk for _, _, chunk in pipeline(text, voice=voice, speed=1.0)])
            sf.write(path, audio, RATE)
        result[key] = {"path": str(path), "seconds": sf.info(path).duration}
    print(json.dumps(result))


if __name__ == "__main__":
    main(sys.argv[1])
