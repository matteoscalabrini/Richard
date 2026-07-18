class RichardError(Exception):
    """Base class for all Richard errors."""


class BrainUnreachable(RichardError):
    """Raised when the llama.cpp brain cannot be reached or errors out."""


class HomeAssistantError(RichardError):
    """Home Assistant could not be reached or returned an invalid response."""
