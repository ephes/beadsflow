from __future__ import annotations


class BeadsflowError(Exception):
    pass


class ConfigError(BeadsflowError):
    pass


class BeadsError(BeadsflowError):
    pass


class LockError(BeadsflowError):
    pass


class CommandError(BeadsflowError):
    pass
