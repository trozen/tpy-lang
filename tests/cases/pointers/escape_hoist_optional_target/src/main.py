# The realistic spelling of the escape: the outer name starts as None
# rather than a sentinel, so it is an Optional pointer-repr local rather
# than a plain pointer-local -- a different binding path that must still
# reach the escape warning.
#
# Kept last-iteration-wins so the case stays CPython-clean: the shapes
# where the hoist gives a different answer than CPython are tracked in
# BUGS.md and cannot be pinned without pinning wrong output.
from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32):
        self.x = x


def optional_target() -> None:
    saved: Point | None = None
    for i in range(3):
        p: Point = Point(i)
        saved = p  # tpyc: warning(/will not keep the object it was given/)
    if saved is not None:
        print(saved.x)


optional_target()
