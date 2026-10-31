from pathlib import Path
import argparse


def run_cli(engine, argv=None):
    
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--vision-test",
        action="store_true",
        help="Request a validated structured-JSON observation of "
        "output/frames/frame_00000.jpg from Baseten's vision model.",
    )
    mode.add_argument(
        "--vision-batch",
        action="store_true",
        help="Analyse up to --limit frames spread across the clip, saving "
        "one validated observation-v1 JSON per frame in output/observations/.",
    )
    mode.add_argument(
        "--migrate-observations",
        action="store_true",
        help="Move validated legacy observation JSONs from output/frames/ "
        "to output/observations/ (verified, idempotent, offline).",
    )
    mode.add_argument(
        "--rebuild-state",
        action="store_true",
        help="Rebuild output/observations/match_state.json from the stored "
        "observation-v1 JSON files (offline, no API calls).",
    )
    mode.add_argument(
        "--detect-events",
        action="store_true",
        help="Detect evidence-grounded events from stored observations and "
        "match_state.json into output/events/events.json (offline).",
    )
    mode.add_argument(
        "--generate-commentary",
        action="store_true",
        help="Generate evidence-grounded commentary lines from stored "
        "observations, match_state.json, and events.json into "
        "output/commentary/commentary.json (offline).",
    )
    mode.add_argument(
        "--commentary-write",
        action="store_true",
        help="Word gated commentary claims with one text-only model call "
        "(paid, opt-in), validating every line and falling back to "
        "deterministic wording on any failure. Writes "
        "output/commentary/commentary.json like --generate-commentary.",
    )
    mode.add_argument(
        "--generate-script",
        action="store_true",
        help="Generate the TTS-ready LLM casting script from stored "
        "observations, match_state.json, and events.json into "
        "output/script/script.json (one bounded text-only model call; "
        "no template fallback).",
    )
    parser.add_argument(
        "--limit",
        type=engine.parse_limit_argument,
        default=None,
        metavar="N",
        help="Maximum frames for --vision-batch (positive integer, "
        f"default {engine.DEFAULT_VISION_BATCH_LIMIT}).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-analyse frames that already have a validated observation JSON.",
    )
    parser.add_argument(
        "--jobs",
        type=engine.parse_jobs_argument,
        default=None,
        metavar="J",
        help="Maximum concurrent vision requests for --vision-batch "
        f"(integer 1-{engine.MAX_VISION_BATCH_JOBS}, default {engine.DEFAULT_VISION_BATCH_JOBS}).",
    )
    parser.add_argument(
        "--video",
        type=Path,
        default=None,
        metavar="PATH",
        help="Gameplay video file for default frame extraction "
        f"(default {engine.VIDEO_PATH}). Only applies to the default mode.",
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
    args = parser.parse_args(argv)
    
    if args.limit is not None and not args.vision_batch:
        parser.error("--limit requires --vision-batch.")
    if args.force and not args.vision_batch:
        parser.error("--force requires --vision-batch.")
    if args.jobs is not None and not args.vision_batch:
        parser.error("--jobs requires --vision-batch.")
    offline_mode = (
        args.vision_test
        or args.vision_batch
        or args.migrate_observations
        or args.rebuild_state
        or args.detect_events
        or args.generate_commentary
        or args.commentary_write
        or args.generate_script
    )
    if args.video is not None and offline_mode:
        parser.error("--video only applies to default frame extraction.")
    
    # Resolve the output tree. None means "use the project-root defaults";
    # the runners below treat None as their standard locations.
    frames_dir = obs_dir = state_path = None
    events_path = commentary_path = script_path = None
    if args.output_dir is not None:
        base = Path(args.output_dir)
        frames_dir = base / "frames"
        obs_dir = base / "observations"
        state_path = obs_dir / engine.MATCH_STATE_FILENAME
        events_path = base / "events" / engine.EVENTS_FILENAME
        commentary_path = base / "commentary" / engine.COMMENTARY_FILENAME
        script_path = base / "script" / engine.SCRIPT_FILENAME
    
    if args.vision_test:
        engine.run_vision_test(
            frame_path=frames_dir / "frame_00000.jpg"
            if frames_dir is not None
            else None,
            observations_dir=obs_dir,
        )
    elif args.vision_batch:
        raise SystemExit(
            engine.run_vision_batch(
                limit=args.limit or engine.DEFAULT_VISION_BATCH_LIMIT,
                force=args.force,
                jobs=args.jobs
                if args.jobs is not None
                else engine.DEFAULT_VISION_BATCH_JOBS,
                directory=frames_dir,
                observations_dir=obs_dir,
            )
        )
    elif args.migrate_observations:
        raise SystemExit(
            engine.run_migration(
                frames_dir=frames_dir, observations_dir=obs_dir
            )
        )
    elif args.rebuild_state:
        raise SystemExit(
            engine.run_state_rebuild(
                observations_dir=obs_dir,
                state_path=state_path,
                frames_dir=frames_dir,
            )
        )
    elif args.detect_events:
        raise SystemExit(
            engine.run_detect_events(
                observations_dir=obs_dir,
                state_path=state_path,
                events_path=events_path,
                frames_dir=frames_dir,
            )
        )
    elif args.generate_commentary:
        raise SystemExit(
            engine.run_generate_commentary(
                observations_dir=obs_dir,
                state_path=state_path,
                events_path=events_path,
                commentary_path=commentary_path,
                frames_dir=frames_dir,
            )
        )
    elif args.commentary_write:
        raise SystemExit(
            engine.run_generate_commentary(
                observations_dir=obs_dir,
                state_path=state_path,
                events_path=events_path,
                commentary_path=commentary_path,
                frames_dir=frames_dir,
                writer="llm",
            )
        )
    elif args.generate_script:
        raise SystemExit(
            engine.run_generate_script(
                observations_dir=obs_dir,
                state_path=state_path,
                events_path=events_path,
                commentary_path=commentary_path,
                script_path=script_path,
                frames_dir=frames_dir,
            )
        )
    else:
        engine.main(video_path=args.video, frames_dir=frames_dir)
    
