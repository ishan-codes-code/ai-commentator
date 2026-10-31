"""Electron worker: orchestrate the AI Commentator engine over stdio.

Launched by the Electron main process as ``python worker.py VIDEO``.
Communicates exclusively with newline-delimited JSON (NDJSON) on stdout;
every line is one JSON object (see ``docs/contracts/worker-protocol.md``).
All engine diagnostics and legacy ``print`` output are redirected to
stderr so stdout stays parseable. No secrets are ever printed.

Exit codes: 0 when every stage succeeds, 1 when a pipeline stage fails,
2 for argument or input validation errors.
"""

import argparse
import contextlib
import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import main as engine  # noqa: E402

SUPPORTED_VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".webm")

REAL_STDOUT = sys.stdout


def emit(message: dict) -> None:
    """Write one protocol message as a single JSON line on stdout."""
    REAL_STDOUT.write(json.dumps(message, ensure_ascii=False) + "\n")
    REAL_STDOUT.flush()


def emit_error(stage: str, message: str) -> None:
    emit({"type": "error", "stage": stage, "message": message})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the AI Commentator pipeline on a video file, "
        "reporting NDJSON progress on stdout.",
    )
    parser.add_argument(
        "video",
        type=Path,
        help="Gameplay video file to process.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="Base output directory holding frames/, observations/, "
        "events/, commentary/, and script/ "
        f"(default {engine.PROJECT_ROOT / 'output'}).",
    )
    parser.add_argument(
        "--limit",
        type=engine.parse_limit_argument,
        default=None,
        metavar="N",
        help="Maximum frames for the vision stage (positive integer, "
        f"default {engine.DEFAULT_VISION_BATCH_LIMIT}).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-analyse frames that already have a validated observation.",
    )
    parser.add_argument(
        "--jobs",
        type=engine.parse_jobs_argument,
        default=None,
        metavar="J",
        help="Maximum concurrent vision requests "
        f"(integer 1-{engine.MAX_VISION_BATCH_JOBS}, "
        f"default {engine.DEFAULT_VISION_BATCH_JOBS}).",
    )
    parser.add_argument(
        "--skip-vision",
        action="store_true",
        help="Skip the paid vision stage and reuse stored observations.",
    )
    parser.add_argument(
        "--with-script",
        action="store_true",
        help="Also generate the final casting script (one paid text-only "
        "model call). Offline stages only by default.",
    )
    return parser


def validate_video(video: Path) -> str | None:
    """Return an error message when the video path is unusable, else None."""
    if not video.exists():
        return f"Video not found: {video}"
    if not video.is_file():
        return f"Not a file: {video}"
    if video.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
        supported = ", ".join(SUPPORTED_VIDEO_EXTENSIONS)
        return (
            f"Unsupported video extension {video.suffix!r} "
            f"(supported: {supported})."
        )
    return None


def count_frames(frames_dir: Path) -> int:
    return len(engine.discover_candidate_frames(frames_dir))


def count_observations(obs_dir: Path) -> int:
    return len(engine.discover_observation_files(obs_dir))


# Skip reasons reported in stage_completed detail. Stable strings:
# the renderer and the contract document rely on them.
SKIP_REASON_OPTION = "option-skipped"
SKIP_REASON_NO_OBSERVATIONS = "no-observations"


def plan_stages(args, obs_dir):
    """Determine the stage plan from flags and available prerequisites.

    Returns ``(stage, mode)`` pairs with mode ``"run"`` or ``"skip"``.
    The plan is a pure function of configuration plus observation
    presence, kept separate from execution so a future adaptive-sampling
    milestone can refine stage selection without touching the runners
    below. No stage is invented: skipped stages are reported, never
    executed, and never counted as completed.
    """
    plan = [("frame_extraction", "run")]
    plan.append(("vision_analysis", "run" if not args.skip_vision else "skip"))
    downstream = ("state_rebuild", "event_detection", "commentary")
    if args.skip_vision and count_observations(obs_dir) == 0:
        # Extraction-only run: vision was skipped and there is nothing
        # valid to analyse. Skipping (not failing) keeps fresh projects
        # usable offline; no fake observations or states are created.
        plan.extend((stage, "skip") for stage in downstream)
    else:
        plan.extend((stage, "run") for stage in downstream)
    if args.with_script:
        plan.append(("script", "run"))
    return plan


def run_mode(plan):
    """Classify a run from its plan: full analysis or extraction-only."""
    downstream = {"state_rebuild", "event_detection", "commentary"}
    if any(stage in downstream and mode == "skip" for stage, mode in plan):
        return "extraction-only"
    return "full"


def read_json_summary(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def require_key() -> str | None:
    if not os.environ.get("BASETEN_API_KEY"):
        return "BASETEN_API_KEY is not set in the environment."
    return None


def run_pipeline(args: argparse.Namespace) -> tuple[int, dict]:
    """Run every stage; return (exit_code, result_data)."""
    base = (
        Path(args.output_dir)
        if args.output_dir is not None
        else engine.PROJECT_ROOT / "output"
    )
    frames_dir = base / "frames"
    obs_dir = base / "observations"
    state_path = obs_dir / engine.MATCH_STATE_FILENAME
    events_path = base / "events" / engine.EVENTS_FILENAME
    commentary_path = base / "commentary" / engine.COMMENTARY_FILENAME
    script_path = base / "script" / engine.SCRIPT_FILENAME

    plan_list = plan_stages(args, obs_dir)
    plan = dict(plan_list)
    total = len(plan_list)
    done: list[str] = []
    skipped: list[str] = []
    events: list = []
    lines: list = []

    def progress(stage: str) -> None:
        emit(
            {
                "type": "progress",
                "stage": stage,
                "completed": len(done),
                "total": total,
                "progress": round(100 * len(done) / total),
            }
        )

    def skip(stage: str, reason: str) -> None:
        skipped.append(stage)
        emit(
            {
                "type": "stage_completed",
                "stage": stage,
                "completed": len(done),
                "total": total,
                "detail": {"skipped": True, "reason": reason},
            }
        )

    def complete(stage: str, detail: dict | None = None) -> None:
        done.append(stage)
        message: dict = {
            "type": "stage_completed",
            "stage": stage,
            "completed": len(done),
            "total": total,
        }
        if detail:
            message["detail"] = detail
        emit(message)

    video = Path(args.video)
    emit({"type": "started", "message": f"Processing video {video.name}"})

    # Frame extraction (offline).
    progress("frame_extraction")
    try:
        with contextlib.redirect_stdout(sys.stderr):
            engine.main(video_path=video, frames_dir=frames_dir)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        emit_error("frame_extraction", f"{exc}")
        return 1, {}
    frame_count = count_frames(frames_dir)
    complete("frame_extraction", {"frames": frame_count})

    # Vision analysis (paid unless skipped).
    if not args.skip_vision:
        progress("vision_analysis")
        try:
            with contextlib.redirect_stdout(sys.stderr):
                code = engine.run_vision_batch(
                    limit=args.limit or engine.DEFAULT_VISION_BATCH_LIMIT,
                    force=args.force,
                    jobs=args.jobs
                    if args.jobs is not None
                    else engine.DEFAULT_VISION_BATCH_JOBS,
                    directory=frames_dir,
                    observations_dir=obs_dir,
                )
        except RuntimeError as exc:
            emit_error("vision_analysis", f"{exc}")
            return 1, {}
        if code != 0:
            emit_error(
                "vision_analysis",
                "Vision batch finished with failures; see worker stderr.",
            )
            return 1, {}
        observation_count = count_observations(obs_dir)
        complete("vision_analysis", {"observations": observation_count})
    else:
        progress("vision_analysis")
        skip("vision_analysis", SKIP_REASON_OPTION)

    # Match-state rebuild (offline).
    progress("state_rebuild")
    if plan["state_rebuild"] == "skip":
        skip("state_rebuild", SKIP_REASON_NO_OBSERVATIONS)
    else:
        with contextlib.redirect_stdout(sys.stderr):
            code = engine.run_state_rebuild(
                observations_dir=obs_dir,
                state_path=state_path,
                frames_dir=frames_dir,
            )
        if code != 0:
            emit_error(
                "state_rebuild",
                "State rebuild failed; see worker stderr.",
            )
            return 1, {}
        complete("state_rebuild", {"state": str(state_path)})

    # Event detection (offline).
    progress("event_detection")
    if plan["event_detection"] == "skip":
        skip("event_detection", SKIP_REASON_NO_OBSERVATIONS)
    else:
        with contextlib.redirect_stdout(sys.stderr):
            code = engine.run_detect_events(
                observations_dir=obs_dir,
                state_path=state_path,
                events_path=events_path,
                frames_dir=frames_dir,
            )
        if code != 0:
            emit_error(
                "event_detection",
                "Event detection failed; see worker stderr.",
            )
            return 1, {}
        events_doc = read_json_summary(events_path) or {}
        events = events_doc.get("events", [])
        complete("event_detection", {"events": len(events)})

    # Commentary generation, deterministic wording (offline).
    progress("commentary")
    if plan["commentary"] == "skip":
        skip("commentary", SKIP_REASON_NO_OBSERVATIONS)
    else:
        with contextlib.redirect_stdout(sys.stderr):
            code = engine.run_generate_commentary(
                observations_dir=obs_dir,
                state_path=state_path,
                events_path=events_path,
                commentary_path=commentary_path,
                frames_dir=frames_dir,
            )
        if code != 0:
            emit_error(
                "commentary",
                "Commentary generation failed; see worker stderr.",
            )
            return 1, {}
        commentary_doc = read_json_summary(commentary_path) or {}
        lines = commentary_doc.get("lines", [])
        complete("commentary", {"lines": len(lines)})

    # Final casting script (paid, opt-in).
    script_status = "skipped"
    if args.with_script:
        progress("script")
        with contextlib.redirect_stdout(sys.stderr):
            code = engine.run_generate_script(
                observations_dir=obs_dir,
                state_path=state_path,
                events_path=events_path,
                commentary_path=commentary_path,
                script_path=script_path,
                frames_dir=frames_dir,
            )
        if code != 0:
            emit_error(
                "script",
                "Script generation failed; see worker stderr.",
            )
            return 1, {}
        script_doc = read_json_summary(script_path) or {}
        script_status = str(script_doc.get("status", "unknown"))
        complete("script", {"status": script_status})

    data = {
        "video": str(video),
        "output_dir": str(base),
        "stages": done,
        "skipped": skipped,
        "mode": run_mode(plan_list),
        "frames": count_frames(frames_dir),
        "observations": count_observations(obs_dir),
        "events": len(events),
        "commentary_lines": len(lines),
        "script": script_status,
        "artifacts": {
            "state": str(state_path) if state_path.is_file() else None,
            "events": str(events_path) if events_path.is_file() else None,
            "commentary": (
                str(commentary_path) if commentary_path.is_file() else None
            ),
            "script": str(script_path) if args.with_script else None,
        },
    }
    return 0, data


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    problem = validate_video(Path(args.video))
    if problem is not None:
        emit_error("validation", problem)
        return 2
    if not args.skip_vision:
        key_problem = require_key()
        if key_problem is not None:
            # Fail fast before any paid call or extraction work: report the
            # missing key as structured output, never the key itself.
            emit_error("vision_analysis", key_problem)
            return 1
    if args.with_script:
        key_problem = require_key()
        if key_problem is not None:
            emit_error("script", key_problem)
            return 1
    try:
        # Engine diagnostics print to stderr from here on; protocol
        # messages keep using the saved real stdout via emit().
        # (run_pipeline also redirects around each engine call, so it is
        # stdout-clean when reused directly.)
        code, data = run_pipeline(args)
    except KeyboardInterrupt:
        emit_error("worker", "Processing was cancelled.")
        return 130
    except Exception as exc:  # defensive: never die without a message
        traceback.print_exc(file=sys.stderr)
        emit_error("worker", f"Unexpected worker failure: {exc}")
        return 1
    if code == 0:
        emit({"type": "result", "data": data})
    return code


if __name__ == "__main__":
    raise SystemExit(main())
