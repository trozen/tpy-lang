# *unpacking on function without *args should error
from tpy import Int32

def add(a: Int32, b: Int32) -> Int32:
    return a + b

def main() -> None:
    items: list[Int32] = [1, 2]
    add(*items)  # tpyc: error(/expects 2/)

main()
