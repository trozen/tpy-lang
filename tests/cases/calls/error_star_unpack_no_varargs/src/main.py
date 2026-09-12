# *unpacking on function without *args should error
from tpy import int32

def add(a: int32, b: int32) -> int32:
    return a + b

def main() -> None:
    items: list[int32] = [1, 2]
    add(*items)  # tpyc: error(/expects 2/)

main()
