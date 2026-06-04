# A free generator function (not async, not a method) with collection-literal
# locals hoisted into the resumable frame -- the generator-free combination the
# async and generator-method cases don't cover. The deferred element-type
# inference (Pending* types) must be resolved before the frame fields render.
from typing import Iterator
from tpy import Int32


def gen() -> Iterator[Int32]:
    nums = [1, 2, 3]
    d = {1: 10, 2: 20}
    s = {7, 8}
    for n in nums:
        yield n
    yield len(d)
    yield len(s)


def main() -> None:
    for v in gen():
        print(v)


main()
