from typing import Final
from tpy import int32


class Box[T]:
    CAPACITY: Final[int32] = 16

    def __init__(self) -> None:
        pass
