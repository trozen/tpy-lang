# Test nonlocal in escaping closure stored in field (must reject)
from typing import Callable
from tpy import Int32

class Counter:
    inc: Callable[[], Int32]

    def __init__(self) -> None:
        count: Int32 = 0
        def increment() -> Int32:  # tpyc: error(/nonlocal.*escaping closure/)
            nonlocal count
            count += 1
            return count
        self.inc = increment
