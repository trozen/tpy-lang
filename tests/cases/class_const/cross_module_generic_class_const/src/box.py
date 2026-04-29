from typing import Final
from tpy import Int32


class Box[T]:
    CAPACITY: Final[Int32] = 16

    def __init__(self) -> None:
        pass
