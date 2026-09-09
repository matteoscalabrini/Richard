class RichardError(Exception):
    """Base class for all Richard errors."""


class BrainUnreachable(RichardError):
    """Raised when the llama.cpp brain cannot be reached or errors out."""


class BrainRejectedInput(BrainUnreachable):
    """The brain answered 4xx to a request carrying images: the model or server cannot
    take that input. Callers speak a specific line and do not retry."""


class HomeAssistantError(RichardError):
    """Home Assistant could not be reached or returned an invalid response."""
