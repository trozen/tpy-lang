# Error: Callable with wrong signature passed to Fn parameter
from typing import Callable
from tpy import Fn, Int32

def apply(f: Fn[[Int32], Int32], x: Int32) -> Int32:
    return f(x)

def main() -> None:
    wrong_type: Callable[[str], Int32] = lambda x: 1
    apply(wrong_type, 1)  # tpyc: error(/Type mismatch/)

main()
