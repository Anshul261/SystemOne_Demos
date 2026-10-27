"""Add smooth push-in zooms to a recorded clip.

    ../.venv/bin/python video/zoom.py video/out/02-live.mp4 video/zooms.json video/out/02-live-zoomed.mp4

zooms.json lists {"start", "end", "x", "y", "zoom", "label"}: seconds, the point to centre on in source
pixels, and the zoom factor. Each zoom eases in over the first EASE seconds and out over the last,
and the crop window never leaves the frame. Crops are sub-pixel, so slow zooms don't jitter.
The caption bar is copied back from the original frame, so captions stay put and readable while zoomed.
"""

import json
import subprocess
import sys

from PIL import Image

W, H, FPS, EASE = 1920, 1080, 30, 0.9


def ease(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return p * p * (3 - 2 * p)  # smoothstep


def window(t: float, zooms: list[dict]) -> tuple[float, float, float, float] | None:
    """The crop box at time t, or None outside every zoom."""
    for z in zooms:
        if z["start"] <= t <= z["end"]:
            amount = min(ease((t - z["start"]) / EASE), ease((z["end"] - t) / EASE))
            scale = 1 + (z["zoom"] - 1) * amount
            w, h = W / scale, H / scale
            cx = W / 2 + (z["x"] - W / 2) * amount
            cy = H / 2 + (z["y"] - H / 2) * amount
            x0 = min(max(cx - w / 2, 0), W - w)
            y0 = min(max(cy - h / 2, 0), H - h)
            return x0, y0, x0 + w, y0 + h
    return None


def caption_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    """The dark caption bar at the top left (one or two lines tall), if one is showing."""
    bottom = 16
    while bottom < 220 and sum(image.getpixel((26, bottom))) < 160:
        bottom += 1
    return (20, 16, 1030, bottom) if bottom > 40 else None


def main(source: str, spec: str, target: str) -> None:
    zooms = json.loads(open(spec).read())
    decode = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", source, "-f", "rawvideo", "-pix_fmt", "rgb24",
                               "-r", str(FPS), "-"], stdout=subprocess.PIPE)
    encode = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                               "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                               "-preset", "medium", "-crf", "18", target], stdin=subprocess.PIPE)
    size, frame = W * H * 3, 0
    while chunk := decode.stdout.read(size):
        if len(chunk) < size:
            break
        box = window(frame / FPS, zooms)
        if box:
            original = Image.frombytes("RGB", (W, H), chunk)
            zoomed = original.resize((W, H), Image.LANCZOS, box=box)
            if caption := caption_box(original):
                zoomed.paste(original.crop(caption), caption[:2])
            chunk = zoomed.tobytes()
        encode.stdin.write(chunk)
        frame += 1
    encode.stdin.close()
    encode.wait()
    decode.wait()
    print(f"{target}: {frame / FPS:.1f} s, {len(zooms)} zooms")


if __name__ == "__main__":
    main(*sys.argv[1:4])
