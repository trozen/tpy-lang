# tpy: ext_module
# Exposes module-level Final constants as CPython module attributes: each is an
# init-time snapshot of the constant value marshalled via to_py. Covers int
# (incl. a value beyond int64 that crosses via the BigInt hex round-trip), float,
# bool, and str. A Final of a non-boundary type (char) is not part of the
# exposed surface -- see ext_checks.py.
from typing import Final
from tpy import char

MAX_SIZE: Final[int] = 100
MIN_SIZE: Final[int] = -7
BIG: Final[int] = 123456789012345678901234567890
RATIO: Final[float] = 1.5
ENABLED: Final[bool] = True
DISABLED: Final[bool] = False
GREETING: Final[str] = "hello"
EMPTY: Final[str] = ""
TAG: Final[char] = "x"
