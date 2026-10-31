from pathlib import Path
import json
import os
import tempfile


def write_json_atomic(destination: Path, data: dict) -> None:
    """Atomically write a JSON document (UTF-8, indented, trailing newline).

    The document is written to a temporary file in the destination directory
    and moved over the target only after the write succeeds, so a failure
    never leaves a partially written JSON file behind.
    """
    rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=destination.name + ".",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(rendered)
        os.replace(tmp_path, destination)
        tmp_path = None
    except BaseException:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


def write_observation_json(destination: Path, validated: dict) -> None:
    """Atomically write a validated observation JSON document."""
    write_json_atomic(destination, validated)


def write_match_state(destination: Path, state: dict) -> None:
    """Atomically write a validated match-state snapshot."""
    write_json_atomic(destination, state)


def copy_verified(source: Path, destination: Path) -> None:
    """Byte-copy a file via temp+replace, verifying the destination bytes.

    The source is never modified. Raises on any failure without leaving a
    partial or corrupt destination file behind.
    """
    data = source.read_bytes()
    tmp_path = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=destination.name + ".",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(data)
        os.replace(tmp_path, destination)
        tmp_path = None
        if destination.read_bytes() != data:
            destination.unlink()
            raise OSError(f"verification read mismatch for {destination}")
    except BaseException:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


