# The `copy()` spelling the escape warning names: it compiles, and it
# means an INDEPENDENT value -- a later write through one name is
# invisible through the other. The sharing alternative is in
# escape_explicit_rc.
from tpy import int32, copy


class Point:
    x: int32

    def __init__(self, x: int32):
        self.x = x


def copy_is_independent() -> None:
    saved = Point(-1)
    for i in range(3):
        p = Point(i)
        if i == 0:
            saved = copy(p)  # tpyc: ok
            p.x = 99
    # The write through `p` did not reach `saved`.
    print(saved.x)


def copy_outlives_the_loop() -> None:
    # The copy is the caller's own object, so it survives every later
    # iteration untouched -- the property the escape reject protects.
    saved = Point(-1)
    for i in range(4):
        p = Point(i)
        if i == 1:
            saved = copy(p)  # tpyc: ok
    saved.x = 77
    print(saved.x)


def copy_into_optional_target() -> None:
    # The idiomatic spelling: the outer name starts as None, so it is an
    # Optional local. copy() has to work through that binding too.
    saved: Point | None = None
    for i in range(3):
        p = Point(i)
        if saved is None:
            saved = copy(p)  # tpyc: ok
            p.x = 99
    if saved is not None:
        print(saved.x)


copy_is_independent()
copy_outlives_the_loop()
copy_into_optional_target()
