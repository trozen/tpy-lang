# Error: Callable with wrong signature passed to Fn parameter
from typing import Callable
from tpy import Fn, int32

def apply(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)

def main() -> None:
    wrong_type: Callable[[str], int32] = lambda x: 1
    apply(wrong_type, 1)  # tpyc: error(/Type mismatch/)

main()
