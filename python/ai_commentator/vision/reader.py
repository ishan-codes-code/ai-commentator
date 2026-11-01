"""Scheduled OpenCV video reader for adaptive sampling.

Opens the source video once per adaptive run, seeks to each scheduled
timestamp on demand, and reads only the current frame -- the video is never
loaded fully into memory and no extracted JPEG set is written before
analysis. ``VideoCapture`` is always released via the context manager (or
``close()``) on every exit path.

OpenCV seeks by timestamp (``CAP_PROP_POS_MSEC``) with backend-dependent
precision: keyframe-based backends may land on a nearby decodable frame
rather than the exact millisecond. The observation timestamp therefore
describes the *scheduled* source-video timestamp, never a frame-perfect
claim about which frame the backend decoded.
"""

import cv2

from ..config import ADAPTIVE_TIMESTAMP_PRECISION


class ScheduledVideoReader:
    """One open video handle serving on-demand scheduled captures."""

    def __init__(self, video_path):
        from pathlib import Path

        self.video_path = Path(video_path)
        self._capture = None
        self.fps = None
        self.frame_count = None
        self.duration = None

    def __enter__(self):
        if not self.video_path.is_file():
            raise FileNotFoundError(f"Video not found: {self.video_path}")
        capture = cv2.VideoCapture(str(self.video_path))
        if not capture.isOpened():
            raise RuntimeError(
                f"Could not open the video: {self.video_path}"
            )
        try:
            fps = capture.get(cv2.CAP_PROP_FPS)
            frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
            if fps <= 0 or frame_count <= 0:
                raise RuntimeError(
                    f"Invalid video metadata for {self.video_path}: "
                    f"fps={fps!r}, frames={frame_count!r}."
                )
            duration = frame_count / fps
            if duration <= 0:
                raise RuntimeError(
                    f"Invalid video duration for {self.video_path}: "
                    f"{duration!r}."
                )
        except Exception:
            capture.release()
            raise
        self._capture = capture
        self.fps = float(fps)
        self.frame_count = float(frame_count)
        self.duration = float(duration)
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False

    def close(self):
        """Release the video handle; idempotent."""
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.release()

    @property
    def is_open(self):
        return self._capture is not None

    def capture_at(self, timestamp):
        """Seek to ``timestamp`` seconds and read one frame.

        Returns the decoded frame (BGR ``ndarray``). Raises
        ``ValueError`` for out-of-range timestamps and ``RuntimeError``
        for failed seeks or reads. Callers must validate the timestamp
        against :attr:`duration` before calling.
        """
        if self._capture is None:
            raise RuntimeError("Video reader is not open.")
        try:
            requested = float(timestamp)
        except (TypeError, ValueError):
            raise ValueError(
                f"Scheduled timestamp must be a number, got {timestamp!r}."
            ) from None
        if requested < 0 or requested >= self.duration:
            raise ValueError(
                f"Scheduled timestamp {requested!r} is outside the video "
                f"range [0, {self.duration})."
            )
        when_ms = round(requested, ADAPTIVE_TIMESTAMP_PRECISION) * 1000.0
        self._capture.set(cv2.CAP_PROP_POS_MSEC, when_ms)
        success, frame = self._capture.read()
        if not success or frame is None or frame.size == 0:
            raise RuntimeError(
                f"Could not read frame at {requested}s "
                f"in {self.video_path}."
            )
        return frame
