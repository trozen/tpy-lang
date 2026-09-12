from typing import Iterable
from tpy import int32, Own
def first[T](items: Own[Iterable[T]]) -> T:
    for x in items:
        return x
    assert False, "empty"

nums = [1, 2]

def main() -> None:
    k = 1
    match k:
        case 1 if first(nums) == 1:
            print("a")
        case _:
            print("b")
    print(len(nums))
main()
