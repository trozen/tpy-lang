# Single-yield generators whose break/continue the simple peephole can't emit
# (for-over-iterable any position, real-loop post-yield) route to resumable.
from tpy import Int32
from typing import Iterator


def take(items: list[Int32], n: Int32) -> Iterator[Int32]:
    i: Int32 = 0
    for x in items:
        if i >= n:
            break
        yield x
        i += 1


def evens(items: list[Int32]) -> Iterator[Int32]:
    for x in items:
        if x % 2 != 0:
            continue
        yield x


def stride() -> Iterator[Int32]:
    # 3-arg range falls to the iterator branch (no counter loop), so even a
    # pre-yield break must route to resumable.
    for i in range(0, 10, 2):
        if i >= 4:
            break
        yield i


def upto_range(n: Int32) -> Iterator[Int32]:
    for i in range(n):
        yield i
        if i >= 2:
            break


def upto_while() -> Iterator[Int32]:
    n: Int32 = 0
    while True:
        yield n
        n += 1
        if n >= 3:
            break


def iter_post_break(items: list[Int32]) -> Iterator[Int32]:
    for x in items:
        yield x
        if x >= 20:
            break


def post_continue(items: list[Int32]) -> Iterator[Int32]:
    # Post-yield continue with observable post-continue code: the resumable
    # path runs it after the consumer resumes (CPython order).
    for x in items:
        yield x
        if x < 0:
            continue
        print(x + 100)


class Limiter:
    limit: Int32

    def __init__(self, limit: Int32) -> None:
        self.limit = limit

    # Generator method break/continue through the method resumable emit path.
    # Iterates a parameter, not a self field -- iterating a self field in a
    # resumable method hits a separate const-iterator gap.
    def first_positives(self, items: list[Int32]) -> Iterator[Int32]:
        c: Int32 = 0
        for x in items:
            if x <= 0:
                continue
            if c >= self.limit:
                break
            yield x
            c += 1


def main() -> None:
    for v in take([10, 20, 30, 40], 2):
        print(v)
    for v in evens([1, 2, 3, 4, 5, 6]):
        print(v)
    for v in stride():
        print(v)
    for v in upto_range(10):
        print(v)
    for v in upto_while():
        print(v)
    for v in iter_post_break([10, 20, 30]):
        print(v)
    for v in post_continue([3, -1, 5]):
        print(v)
    lim = Limiter(2)
    nums: list[Int32] = [-1, 5, -2, 7, 9]
    for v in lim.first_positives(nums):
        print(v)


main()
