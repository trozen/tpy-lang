from typing import ClassVar
from tpy import int32


class Counters:
    total: ClassVar[int32] = 0


def bump_from_other_module() -> None:
    Counters.total += 100
