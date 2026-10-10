"""Domain errors are mapped to transport-specific status codes at the API boundary."""


class VersionConflict(ValueError):
    """An Agent changed since the caller last read its current version."""


class AgentNotFound(LookupError):
    """No Agent with the requested identifier exists."""
