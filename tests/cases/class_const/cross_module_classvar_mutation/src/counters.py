from typing import ClassVar
from tpy import Int32


class Counters:
    total: ClassVar[Int32] = 0


def bump_from_other_module() -> None:
    Counters.total += 100
