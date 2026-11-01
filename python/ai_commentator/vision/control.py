"""Stdin control channel for the Electron worker (M4.2-A foundation).

The Electron main process owns one pipe to the worker: the child's
standard input. It carries only small, versioned, newline-delimited JSON
control messages; stdout stays exclusively NDJSON worker messages and
stderr stays diagnostics. The only command in version 1 is ``stop``,
which routes to the existing adaptive :class:`StopToken`
(:mod:`python.ai_commentator.vision.adaptive`) — no second cancellation
mechanism is built here.

A daemon reader thread pumps ``stream`` line by line, so waiting for
input never blocks the adaptive loop. Line buffering reassembles commands
split across arbitrary input chunks; EOF (channel closure) ends the
thread quietly. Reading is the thread's only job: it never touches
stdout, never executes anything, and never logs message payloads.
"""

import json
import sys
import threading

CONTROL_PROTOCOL_VERSION = 1
CONTROL_TYPE_STOP = "stop"

# A stop command is dozens of bytes; anything larger cannot be a valid
# version-1 message and is rejected without parsing or logging it.
MAX_CONTROL_LINE_CHARS = 4096


def interpret_control_line(line):
    """Interpret one stdin control line; return ``"stop"`` or ``None``.

    Never raises and never echoes the payload: rejected lines produce a
    short fixed-shape diagnostic on stderr (category plus length only),
    and blank lines are ignored silently. Extra fields on an otherwise
    valid stop command are ignored for forward compatibility.
    """
    if not isinstance(line, str):
        print(
            "Control: ignoring non-text control input.",
            file=sys.stderr,
        )
        return None
    text = line.strip()
    if not text:
        return None
    if len(text) > MAX_CONTROL_LINE_CHARS:
        print(
            f"Control: ignoring over-long control message ({len(text)} chars).",
            file=sys.stderr,
        )
        return None
    try:
        message = json.loads(text)
    except ValueError:
        print(
            f"Control: ignoring malformed control message ({len(text)} chars).",
            file=sys.stderr,
        )
        return None
    if not isinstance(message, dict):
        print(
            f"Control: ignoring malformed control message ({len(text)} chars).",
            file=sys.stderr,
        )
        return None
    if message.get("type") != CONTROL_TYPE_STOP:
        print(
            f"Control: ignoring unknown control command ({len(text)} chars).",
            file=sys.stderr,
        )
        return None
    version = message.get("version")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != CONTROL_PROTOCOL_VERSION
    ):
        print(
            f"Control: ignoring unsupported control version ({len(text)} chars).",
            file=sys.stderr,
        )
        return None
    print("Control: stop requested.", file=sys.stderr)
    return CONTROL_TYPE_STOP


class ControlListener:
    """Background stdin pump forwarding stop commands to a live token.

    The token cell is lock-guarded so the worker can ``detach()`` it the
    moment the adaptive run ends: late or repeated commands after that
    point are accepted from the stream but affect nothing. The thread is
    a daemon — it cannot keep the process alive — and ends on EOF or on
    a broken stream without raising.
    """

    def __init__(self, stream, token=None):
        self._stream = stream
        self._lock = threading.Lock()
        self._token = token
        self._thread = None

    def start(self):
        """Start the pump thread once; further calls are no-ops."""
        if self._thread is not None:
            return self
        thread = threading.Thread(
            target=self._pump,
            name="worker-control",
            daemon=True,
        )
        thread.start()
        self._thread = thread
        return self

    def detach(self):
        """Forget the bound token; later commands become no-ops."""
        with self._lock:
            self._token = None

    @property
    def thread(self):
        """The pump thread, or ``None`` before :meth:`start`."""
        return self._thread

    def _pump(self):
        try:
            for line in self._stream:
                if interpret_control_line(line) != CONTROL_TYPE_STOP:
                    continue
                with self._lock:
                    token = self._token
                if token is not None:
                    token.request_stop()
        except (OSError, ValueError):
            # stdin closed or broken mid-read: nothing left to listen for.
            pass


def start_control_listener(token, stream=None):
    """Start a daemon stdin listener bound to ``token``.

    Returns the :class:`ControlListener`, or ``None`` when no readable
    stream exists (embedded interpreters, closed stdin). Call
    :func:`detach_control_listener` when the adaptive run ends.
    """
    if stream is None:
        stream = sys.stdin
    if stream is None:
        return None
    return ControlListener(stream, token).start()


def detach_control_listener(listener):
    """Detach a listener started by :func:`start_control_listener`."""
    if listener is not None:
        listener.detach()
