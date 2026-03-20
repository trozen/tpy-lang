# Test error: nonlocal in escaping closure
from typing import Callable
from tpy import Int32

def make_counter() -> Callable[[], Int32]:
    count: Int32 = 0
    def increment() -> Int32:  # tpyc: error(/nonlocal.*escaping/)
        nonlocal count
        count += 1
        return count
    return increment

def main() -> None:
    pass

main()
