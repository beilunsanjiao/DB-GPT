"""Stable errors raised by the read-only SQL guard."""


class SqlGuardError(ValueError):
    """A safe, machine-readable SQL rejection."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
