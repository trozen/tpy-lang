from typing import Iterable, Iterator
from tpy import int32, NativeIterable
def gen(it: Iterable[int32]) -> Iterator[int32]:
    assert isinstance(it, NativeIterable)
    for x in it:
        yield x
def main() -> None:
    pass
main()
