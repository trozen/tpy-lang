# `Rc` gives one object both names reach, which is what CPython's aliasing
# does: a write through either handle is visible through the other, and the
# object outlives the iteration that created it. Not what the escape
# warning suggests -- it names only copy() -- but the shape a user reaches
# for when the two names genuinely must share, so it is pinned here.
#
# Reads and writes go through `.get()` rather than TPy's transparent
# deref, which keeps the case runnable under CPython -- the parity check
# is the point of the case, and the deref sugar is not what it tests.
from tplib.rc import Rc

from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32):
        self.x = x


def rc_is_shared() -> None:
    saved = Rc.new(Point(-1))
    for i in range(3):
        p = Rc.new(Point(i))
        if i == 0:
            saved = p.clone()  # tpyc: ok
            p.get().x = 99
    # The write through `p` IS visible through `saved` -- same object.
    print(saved.get().x)


def rc_outlives_the_loop() -> None:
    saved = Rc.new(Point(-1))
    for i in range(4):
        p = Rc.new(Point(i))
        if i == 1:
            saved = p.clone()  # tpyc: ok
    saved.get().x = 77
    print(saved.get().x)


rc_is_shared()
rc_outlives_the_loop()
