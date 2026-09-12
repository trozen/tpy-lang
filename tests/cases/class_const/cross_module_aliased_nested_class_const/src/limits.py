from typing import Final
from tpy import int32


class Limits:
    class Inner:
        MAX: Final[int32] = 99
        TAG: Final[str] = "inner-aliased"
