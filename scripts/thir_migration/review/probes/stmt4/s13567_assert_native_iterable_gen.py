from typing import Iterable, Iterator
from tpy import Int32, NativeIterable
def gen(it: Iterable[Int32]) -> Iterator[Int32]:
    assert isinstance(it, NativeIterable)
    for x in it:
        yield x
def main() -> None:
    pass
main()
