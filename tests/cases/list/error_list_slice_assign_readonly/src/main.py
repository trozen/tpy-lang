# Error: slice assignment on a readonly list.
from tpy import Int32, readonly

def take_readonly(a: readonly[list[Int32]]) -> None:
    a[1:3] = [10, 20]  # tpyc: error(/Cannot mutate readonly/)

def main() -> None:
    a: list[Int32] = [1, 2, 3]
    take_readonly(a)

main()
