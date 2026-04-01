# Own[Iterable[T]] on a generic generator: codegen must unwrap Own to detect
# the protocol param and generate the correct template header.
# The "never consumed" warning is expected -- Own is not needed here, but the
# codegen path must still handle it without crashing.
from tpy import Own, Int32
from typing import Iterable, Iterator

def indexed[T](items: Own[Iterable[T]]) -> Iterator[tuple[Int32, T]]:  # tpyc: warning(/never consumed/)
    i: Int32 = 0
    for item in items:
        yield (i, item)
        i += 1

def main() -> None:
    nums: list[Int32] = [10, 20, 30]
    for i, n in indexed(nums):
        print(i, n)

    words: list[str] = ["hello", "world"]
    for i, w in indexed(words):
        print(i, w)

main()
