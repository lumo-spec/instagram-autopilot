"""Error types used across the package."""


class AutopilotError(Exception):
    """Base class for every handled failure in the autopilot."""


class ConfigError(AutopilotError):
    """config.yaml or a page file is missing or invalid."""


class ContentError(AutopilotError):
    """A content bank entry is invalid or no entry can be selected."""


class FitError(AutopilotError):
    """Text cannot be fitted into its box at or above the minimum font size."""


class RenderError(AutopilotError):
    """Rendering media (image, audio or video) failed."""


class PublishError(AutopilotError):
    """The Instagram API refused or failed a publishing step."""


class TransientPublishError(PublishError):
    """A publishing step failed in a way that is worth retrying."""


class ValidationError(AutopilotError):
    """Data handed between jobs failed re-validation."""


class ArtError(AutopilotError):
    """Automatic art generation or its safety check failed (message never holds a key)."""


class InboxError(AutopilotError):
    """The video inbox is not set up correctly (missing folder, bad inbox.yaml, ...)."""


class BudgetError(ArtError):
    """The next paid art call could go over the monthly cost cap."""
