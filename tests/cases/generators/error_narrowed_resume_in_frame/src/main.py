# An isinstance narrowing that must survive a resume point: the frame branch
# cannot be constexpr, and the resumable lowering has no runtime-concept render.
from typing import Iterator, Sized
from tpy import Int32


def gen(items: Sized) -> Iterator[Int32]:  # tpyc: error(/res.narrowed_resume/)
    if isinstance(items, Sized):
        yield 1
    yield 2


def main() -> None:
    for v in gen([1]):
        print(v)


main()
