# Test error: nonlocal in escaping closure
from typing import Callable
from tpy import int32

def make_counter() -> Callable[[], int32]:
    count: int32 = 0
    def increment() -> int32:  # tpyc: error(/nonlocal.*escaping/)
        nonlocal count
        count += 1
        return count
    return increment

def main() -> None:
    pass

main()
