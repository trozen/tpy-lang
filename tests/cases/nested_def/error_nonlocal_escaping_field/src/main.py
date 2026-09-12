# Test nonlocal in escaping closure stored in field (must reject)
from typing import Callable
from tpy import int32

class Counter:
    inc: Callable[[], int32]

    def __init__(self) -> None:
        count: int32 = 0
        def increment() -> int32:  # tpyc: error(/nonlocal.*escaping closure/)
            nonlocal count
            count += 1
            return count
        self.inc = increment
