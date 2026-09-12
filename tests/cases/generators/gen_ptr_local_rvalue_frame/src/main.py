# An rvalue written into a pointer-form frame local gets a frame-field
# materialization slot (was: a case-block slot dying at the next
# suspension while the pointer field kept aiming at it -- silent UAF).
from typing import Iterator, Optional
from tpy import int32, Own


class Point:
    def __init__(self, x: int32) -> None:
        self.x = x


def make_opt(n: int32) -> Own[Optional[Point]]:
    if n > 0:
        return Point(n)
    return None


def rvalue_init() -> Iterator[int32]:
    # The Point(42) slot must survive the loop's suspensions.
    saved: Optional[Point] = Point(42)
    for i in range(3):
        yield i
    if saved is not None:
        yield saved.x


def rebind_after_alias(items: list[Point]) -> Iterator[int32]:
    saved: Optional[Point] = None
    saved = items[0]
    # The alias write stays an alias: this mutation reaches items[0].
    if saved is not None:
        saved.x += 10
    yield 1
    # The rvalue rebind gets its own frame slot; the alias target above
    # is untouched by it.
    saved = Point(9)
    yield 2
    if saved is not None:
        yield saved.x


def rebind_after_none() -> Iterator[int32]:
    saved: Optional[Point] = None
    yield 1
    saved = Point(5)
    yield 2
    if saved is not None:
        yield saved.x


def own_opt_call() -> Iterator[int32]:
    # The returned optional<Point> lives in a frame slot; the pointer
    # local is lifted from it and read after two suspensions.
    got = make_opt(3)
    yield 1
    yield 2
    if got is not None:
        yield got.x


def loop_rebind(n: int32) -> Iterator[int32]:
    # The same rebind site re-executed each iteration reuses one frame
    # slot: the assignment destroys the prior payload at the rebind.
    saved: Optional[Point] = None
    i = 0
    while i < n:
        saved = Point(i * 100)
        yield i
        if saved is not None:
            yield saved.x
        i += 1


def main() -> None:
    for v in rvalue_init():
        print(v)
    pts = [Point(1)]
    for v in rebind_after_alias(pts):
        print(v)
    print(pts[0].x)
    for v in rebind_after_none():
        print(v)
    for v in own_opt_call():
        print(v)
    for v in loop_rebind(2):
        print(v)


main()
