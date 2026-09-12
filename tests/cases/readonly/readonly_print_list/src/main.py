# print() works on readonly[list[T]] -- codegen unwraps ReadonlyType for ListPrinter.
from tpy import int32, readonly

def show(l: readonly[list[int32]]) -> None:
    print(l)

def main() -> None:
    nums: list[int32] = [1, 2, 3]
    show(nums)

main()
