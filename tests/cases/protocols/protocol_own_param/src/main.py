# Own[Protocol[T]] on a plain function: codegen must unwrap Own to detect
# the protocol param and generate the correct template header.
from tpy import Own, int32
from typing import Iterable

def first[T](items: Own[Iterable[T]]) -> T:  # tpyc: warning(/never consumed/)
    for x in items:
        return x
    assert False, "empty"

def to_list[T](items: Own[Iterable[T]]) -> Own[list[T]]:  # tpyc: warning(/never consumed/)
    result: list[T] = []
    for x in items:
        result.append(x)
    return result

def main() -> None:
    nums: list[int32] = [1, 2, 3]
    print(first(nums))

    words: list[str] = ["hello", "world"]
    print(first(words))
    print(to_list(words))

main()
