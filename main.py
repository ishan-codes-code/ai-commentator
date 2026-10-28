import argparse
import base64
import mimetypes
import os
import sys
from pathlib import Path

import cv2
import numpy as np
from openai import APIError, OpenAI

VIDEO_PATH = Path("gameplay/match.mp4")
OUTPUT_DIR = Path("output/frames")
VISION_FRAME_PATH = OUTPUT_DIR / "frame_00000.jpg"
INTERVAL = 3
BASETEN_BASE_URL = "https://inference.baseten.co/v1"
BASETEN_MODEL = "meta-models/muse-spark-open-1026"
VISION_PROMPT = (
    "Describe only the visible evidence in this Free Fire gameplay frame. Cover "
    "the environment, characters, visible HUD elements, and apparent combat "
    "activity. Clearly distinguish direct observations from uncertain "
    "interpretations, labeling interpretations as uncertain. Do not invent "
    "events, actions, identities, or outcomes that are not visible in this "
    "single frame. Say when something is unreadable or ambiguous."
)


def main():
    if not VIDEO_PATH.is_file():
        raise FileNotFoundError(f"Video not found: {VIDEO_PATH.resolve()}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    video = cv2.VideoCapture(str(VIDEO_PATH))

    if not video.isOpened():
        raise RuntimeError("Could not open the video.")

    try:
        fps = video.get(cv2.CAP_PROP_FPS)
        total_frames = video.get(cv2.CAP_PROP_FRAME_COUNT)

        if fps <= 0 or total_frames <= 0:
            raise RuntimeError("Invalid video metadata.")

        duration = total_frames / fps

        print(f"FPS: {fps:.2f}")
        print(f"Frames: {int(total_frames)}")
        print(f"Duration: {duration:.2f} seconds")

        timestamp = 0
        saved_count = 0

        while timestamp < duration:
            video.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            success, frame = video.read()

            if not success:
                print(f"Could not read frame at {timestamp}s")
                timestamp += INTERVAL
                continue

            output_path = OUTPUT_DIR / f"frame_{timestamp:05d}.jpg"

            if not cv2.imwrite(str(output_path), frame):
                raise RuntimeError(f"Could not save {output_path}")

            output_path.chmod(0o644)
            print(f"Saved: {output_path}")
            saved_count += 1
            timestamp += INTERVAL

        print(f"Finished: {saved_count} frames saved.")
    finally:
        video.release()


def run_vision_test():
    api_key = os.environ.get("BASETEN_API_KEY")
    if not api_key:
        raise RuntimeError("BASETEN_API_KEY is not set in the container environment.")

    if not VISION_FRAME_PATH.is_file():
        raise FileNotFoundError(f"Frame not found: {VISION_FRAME_PATH}")

    image_bytes = VISION_FRAME_PATH.read_bytes()
    if not image_bytes:
        raise ValueError(f"Frame is empty: {VISION_FRAME_PATH}")

    image = cv2.imdecode(
        np.frombuffer(image_bytes, dtype=np.uint8),
        cv2.IMREAD_UNCHANGED,
    )
    if image is None or image.size == 0:
        raise ValueError(f"Frame is not a valid image: {VISION_FRAME_PATH}")

    mime_type, _ = mimetypes.guess_type(VISION_FRAME_PATH.name)
    if not mime_type or not mime_type.startswith("image/"):
        raise ValueError(f"Could not determine image MIME type: {VISION_FRAME_PATH}")

    height, width = image.shape[:2]
    print(f"Validated frame: {VISION_FRAME_PATH} ({width}x{height}, {mime_type})")
    image_data_url = (
        f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    )

    client = OpenAI(api_key=api_key, base_url=BASETEN_BASE_URL)
    try:
        raw_response = client.chat.completions.with_raw_response.create(
            model=BASETEN_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": image_data_url},
                        },
                    ],
                }
            ],
        )
        print(f"Baseten API response: HTTP {raw_response.status_code}")
        response = raw_response.parse()
    except APIError as exc:
        status_code = getattr(exc, "status_code", None)
        status = f"HTTP {status_code}" if status_code else "request error"
        detail = getattr(exc, "message", None) or str(exc)
        if exc.__cause__:
            detail = f"{detail} Cause: {exc.__cause__}"
        detail = detail.replace(api_key, "[REDACTED]")
        print(f"Baseten API {status}: {detail}", file=sys.stderr)
        raise SystemExit(1) from None

    text = response.choices[0].message.content
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("Baseten returned no text content.")

    print(text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--vision-test",
        action="store_true",
        help="Send output/frames/frame_00000.jpg to Baseten's vision model.",
    )
    args = parser.parse_args()

    if args.vision_test:
        run_vision_test()
    else:
        main()
