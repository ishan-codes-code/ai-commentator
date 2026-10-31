class ObservationError(ValueError):
    """Model output failed observation-v1 parsing or validation."""


class VisionAPIError(RuntimeError):
    """Baseten vision request failed; the message is already redacted."""


class WriterAPIError(RuntimeError):
    """Baseten commentary-writer request failed; the message is already redacted."""


