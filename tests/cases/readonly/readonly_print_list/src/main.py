# print() works on readonly[list[T]] -- codegen unwraps ReadonlyType for ListPrinter.
from tpy import Int32, readonly

def show(l: readonly[list[Int32]]) -> None:
    print(l)

def main() -> None:
    nums: list[Int32] = [1, 2, 3]
    show(nums)

main()
