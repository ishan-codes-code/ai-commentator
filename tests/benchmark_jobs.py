"""Synthetic sequential-vs-concurrent timing comparison (NOT a unit test).

Uses fixture JPEGs and a fake vision client that sleeps a fixed delay per
request, so no Baseten calls, credentials, or network are involved. Every
number printed here is SYNTHETIC: it measures lock-step sleeps under a fake
client, not real model latency, and must never be quoted as a production
speedup. Run natively from the repository root:
    .venv/bin/python tests/benchmark_jobs.py
"""

import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "python"))
sys.path.insert(0, str(TESTS_DIR))

from test_vision_batch import (  # noqa: E402
    FakeChoice,
    FakeCompletion,
    FakeMessage,
    FakeRawResponse,
    make_frames,
    observation_text,
)

from main import run_vision_batch  # noqa: E402

REQUEST_DELAY = 0.3
TIMESTAMPS = [0, 3, 6, 9, 12]


class SleepyCompletions:
    def __init__(self):
        self.calls = []

    @property
    def with_raw_response(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = kwargs["messages"][0]["content"][0]["text"]
        timestamp = int(text.split("frame is at ")[1].split(" seconds")[0])
        time.sleep(REQUEST_DELAY)
        return FakeRawResponse(
            FakeCompletion([FakeChoice(FakeMessage(observation_text(timestamp)))])
        )


class SleepyClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SleepyCompletions())


def run_once(jobs):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        frames = root / "frames"
        obs = root / "observations"
        obs.mkdir(parents=True, exist_ok=True)
        make_frames(frames, TIMESTAMPS)
        client = SleepyClient()
        started = time.perf_counter()
        code = run_vision_batch(
            limit=len(TIMESTAMPS),
            jobs=jobs,
            client=client,
            directory=frames,
            observations_dir=obs,
        )
        elapsed = time.perf_counter() - started
        payloads = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(obs.glob("frame_*.json"))
        }
    return code, elapsed, payloads, len(client.chat.completions.calls)


def main():
    print("SYNTHETIC benchmark: fake client sleeps "
          f"{REQUEST_DELAY}s per request, no network calls.")
    results = {}
    for jobs in (1, 4):
        code, elapsed, payloads, calls = run_once(jobs)
        results[jobs] = payloads
        print(f"SYNTHETIC jobs={jobs}: exit={code} frames={len(payloads)} "
              f"requests={calls} elapsed={elapsed:.2f}s")
    assert results[1] == results[4], "sequential/concurrent outputs diverged"
    print("SYNTHETIC check passed: jobs=1 and jobs=4 outputs are identical.")


if __name__ == "__main__":
    main()
