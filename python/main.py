"""Compatibility facade for the AI Commentator engine and legacy imports."""

from pathlib import Path
import sys

from ai_commentator.commentary.claims import (
    _commentary_fact_claim,
    _commentary_uncertainty_tokens,
    gate_commentary_claims,
)
from ai_commentator.commentary.generation import (
    run_generate_commentary as _run_generate_commentary,
    load_prior_commentary,
)
from ai_commentator.commentary.writer import (
    _render_claim_line,
    build_writer_prompt,
    claim_id_of,
    order_commentary_claims,
    parse_writer_lines,
    prioritize_writer_claims,
    render_claim_text,
    render_commentary_claims,
    render_writer_claims,
    request_writer_text,
    run_writer_path,
    writer_text_valid,
)
from ai_commentator.commentary.validation import (
    _verify_commentary_provenance,
    _verify_commentary_template,
    commentary_claim_id,
    commentary_hash,
    commentary_line_id,
    validate_commentary,
)
from ai_commentator.config import (
    ADAPTIVE_DEFAULT_MAX_FRAMES,
    ADAPTIVE_DEFAULT_SPEED,
    ADAPTIVE_HARD_MAX_FRAMES,
    ADAPTIVE_INTERVALS,
    ADAPTIVE_MAX_INTERVAL,
    ADAPTIVE_MAX_REASON_CHARS,
    ADAPTIVE_MIN_INTERVAL,
    ADAPTIVE_OBSERVATION_FILENAME_RE,
    ADAPTIVE_REQUEST_TIMEOUT_S,
    ADAPTIVE_RUN_MANIFEST_FILENAME,
    ADAPTIVE_RUN_SCHEMA_VERSION,
    ADAPTIVE_SPEEDS,
    ADAPTIVE_TIMESTAMP_PRECISION,
    BASETEN_BASE_URL,
    BASETEN_MODEL,
    COMMENTARY_DIR,
    COMMENTARY_FILENAME,
    COMMENTARY_GATE_RULES,
    COMMENTARY_PATH,
    COMMENTARY_PRIOR_STATUSES,
    COMMENTARY_QUALIFIERS,
    COMMENTARY_SCENE_CATEGORIES,
    COMMENTARY_SCHEMA_VERSION,
    COMMENTARY_TEMPLATES,
    COMMENTARY_TYPES,
    DEFAULT_VISION_BATCH_JOBS,
    DEFAULT_VISION_BATCH_LIMIT,
    EVENT_ELIM_RE,
    EVENT_FEED_MENTIONS,
    EVENT_NAME_RE,
    EVENT_RULES,
    EVENT_SCHEMA_VERSION,
    EVENT_STATUSES,
    EVENT_TEAM_SCOPE_RE,
    EVENT_TRIGGER_CONFIDENCES,
    EVENTS_DIR,
    EVENTS_FILENAME,
    EVENTS_PATH,
    FRAME_FILENAME_RE,
    FRAMES_DIR,
    INTERVAL,
    MATCH_STATE_CHANGE_KINDS,
    MATCH_STATE_FILENAME,
    MATCH_STATE_PATH,
    MATCH_STATE_SCHEMA_VERSION,
    MATCH_STATE_STATUSES,
    MAX_VISION_BATCH_JOBS,
    OBSERVATION_CATEGORIES,
    OBSERVATION_CONFIDENCES,
    OBSERVATION_FILENAME_RE,
    OBSERVATION_OUTPUT_PATH,
    OBSERVATION_SCHEMA_VERSION,
    OBSERVATIONS_DIR,
    PROJECT_ROOT,
    SCRIPT_ACTION_STEMS,
    SCRIPT_CAUSAL_MARKERS,
    SCRIPT_CERTAINTY_MARKERS,
    SCRIPT_DIR,
    SCRIPT_FILENAME,
    SCRIPT_INTENT_MARKERS,
    SCRIPT_KILL_STEMS,
    SCRIPT_MAX_CLAIMS,
    SCRIPT_MAX_HISTORY_LINES,
    SCRIPT_MAX_TEXT_CHARS,
    SCRIPT_MIN_TEXT_CHARS,
    SCRIPT_OUTCOME_STEMS,
    SCRIPT_PATH,
    SCRIPT_SCHEMA_VERSION,
    SCRIPT_STATUSES,
    SCRIPT_UNCERTAINTY_MARKERS,
    SCRIPT_WORD_NUMBERS,
    SCRIPT_ZONE_STEMS,
    VIDEO_PATH,
    VISION_FRAME_PATH,
    WRITER_MAX_CLAIMS,
    WRITER_MAX_EVIDENCE_CHARS,
    WRITER_MAX_HISTORY_LINES,
)
from ai_commentator.errors import ObservationError, VisionAPIError, WriterAPIError
from ai_commentator.events.detection import (
    _elim_readings,
    _feed_name_tokens,
    _mentions_feed,
    detect_events,
    event_id,
    validate_events,
)
from ai_commentator.events.runner import run_detect_events
from ai_commentator.script.generation import (
    _script_norm,
    _script_numbers,
    _stem_present,
    build_script_prompt,
    load_prior_script,
    parse_script_segments,
    script_segment_id,
    script_text_valid,
    validate_script,
)
from ai_commentator.script.runner import run_generate_script as _run_generate_script
from ai_commentator.state.match_state import (
    apply_observation,
    build_match_state,
    fact_key,
    finalize_match_state,
    new_match_state_builder,
    observation_sha256,
    validate_match_state,
)
from ai_commentator.state.runner import run_state_rebuild
from ai_commentator.storage import (
    copy_verified,
    write_json_atomic,
    write_match_state,
    write_observation_json,
)
from ai_commentator.vision import adaptive as _adaptive
from ai_commentator.vision.adaptive import StopToken, parse_max_frames_argument
from ai_commentator.vision import batch as _vision_batch
from ai_commentator.vision.batch import (
    migrate_observations,
    read_validated_observation,
    run_migration,
    process_batch_frame,
)
from ai_commentator.vision.client import (
    analyze_adaptive_frame,
    analyze_frame,
    build_adaptive_vision_prompt,
    build_image_data_url,
    build_previous_context,
    build_vision_prompt,
    encode_frame_image,
    ensure_observation_timestamp,
    extract_response_text,
    load_frame_image,
    request_adaptive_text,
    request_observation_text,
    truncate_context_text,
    vision_frame_timestamp,
)
from ai_commentator.vision.frames import (
    discover_candidate_frames,
    discover_observation_files,
    extract_frames,
    find_legacy_observations,
    observation_path_for_frame,
    parse_frame_timestamp,
    parse_jobs_argument,
    parse_limit_argument,
    parse_observation_timestamp,
    select_spread_frames,
    warn_about_legacy_observations,
)
from ai_commentator.vision.observations import (
    _reject_extra,
    _require_non_empty_string,
    parse_observation,
    validate_observation,
)
from ai_commentator.vision.control import (
    CONTROL_PROTOCOL_VERSION,
    CONTROL_TYPE_STOP,
    MAX_CONTROL_LINE_CHARS,
    ControlListener,
    detach_control_listener,
    interpret_control_line,
    start_control_listener,
)
from ai_commentator.vision.reader import ScheduledVideoReader
from ai_commentator.vision.sampling import (
    AdaptiveScheduler,
    format_adaptive_timestamp,
    observation_path_for_timestamp,
    parse_adaptive_response,
    validate_intervals,
    validate_sampling,
)
from openai import OpenAI


def main(video_path: Path | None = None, frames_dir: Path | None = None):
    """Preserve the original frame-extraction entry point."""
    source = VIDEO_PATH if video_path is None else Path(video_path)
    output = FRAMES_DIR if frames_dir is None else Path(frames_dir)
    return extract_frames(source, output)


def run_generate_commentary(*args, **kwargs):
    """Compatibility wrapper preserving patchable provider construction."""
    from ai_commentator.commentary import generation as commentary_generation

    commentary_generation.OpenAI = OpenAI
    return _run_generate_commentary(*args, **kwargs)


def run_generate_script(*args, **kwargs):
    """Compatibility wrapper preserving legacy patch points."""
    from ai_commentator.script import runner as script_generation

    script_generation.write_json_atomic = write_json_atomic
    script_generation.OpenAI = OpenAI
    return _run_generate_script(*args, **kwargs)


def run_vision_test(*args, **kwargs):
    """Compatibility wrapper preserving the legacy OpenAI patch point."""
    _vision_batch.OpenAI = OpenAI
    return _vision_batch.run_vision_test(*args, **kwargs)


def run_vision_batch(*args, **kwargs):
    """Compatibility wrapper preserving the legacy OpenAI patch point."""
    _vision_batch.OpenAI = OpenAI
    return _vision_batch.run_vision_batch(*args, **kwargs)


def run_adaptive_vision(*args, **kwargs):
    """Compatibility wrapper preserving the legacy OpenAI patch point."""
    _adaptive.OpenAI = OpenAI
    return _adaptive.run_adaptive_vision(*args, **kwargs)


if __name__ == "__main__":
    from ai_commentator.cli import run_cli

    run_cli(sys.modules[__name__])
