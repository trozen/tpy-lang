"""The CPython stub's Deref forwarding in the shapes the compiler cannot run
yet (BUGS.md#deref-subclass-member-rejects): a wrapper subclass keeps an
annotated field it inherits, and a dropped wrapper class is freed."""

import gc
import weakref

from tpy import Deref


class Point:
    def __init__(self, x: int) -> None:
        self.x = x


class Base(Deref[Point]):
    target: Point
    tag: str

    def __init__(self, target: Point) -> None:
        self.target = target

    def __deref__(self) -> Point:
        return self.target


class Sub(Base):
    pass


def test_inherited_annotated_field_stays_on_the_wrapper() -> None:
    s = Sub(Point(1))
    s.tag = "t"  # annotated on Base, first written outside __init__
    s.x = 5
    assert (s.tag, s.x, s.target.x) == ("t", 5, 5)
    assert "tag" in vars(s) and "x" not in vars(s)


def test_dropped_wrapper_class_is_collected() -> None:
    class Local(Base):
        pass

    Local(Point(1)).x = 2  # fills the per-class caches
    ref = weakref.ref(Local)
    del Local
    gc.collect()
    assert ref() is None


def test_class_member_added_later_counts() -> None:
    class Late(Base):
        pass

    h = Late(Point(1))
    h.x = 2  # forwards, before the class declares `x`
    Late.x = 10
    h.x = 20
    assert (vars(h).get("x"), h.target.x) == (20, 2)
