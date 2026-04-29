from typing import Final
from tpy import Int32


class Limits:
    class Inner:
        MAX: Final[Int32] = 99
        TAG: Final[str] = "inner-cross"
