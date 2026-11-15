"""Injectable clock (design §16). Tests call ``clock.set(epoch)`` / ``clock.advance(sec)``."""
import time as _time

_fixed = None


def now() -> int:
    return int(_fixed if _fixed is not None else _time.time())


def set(epoch):  # noqa: A001 - mirrors the design wording
    global _fixed
    _fixed = epoch


def advance(seconds):
    global _fixed
    _fixed = (now() if _fixed is None else _fixed) + seconds


def reset():
    global _fixed
    _fixed = None
