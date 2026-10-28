"""The CPython stub's Deref forwarding in the shapes the compiler cannot run
yet (BUGS.md#deref-subclass-member-rejects): a wrapper subclass keeps an
annotated field it inherits, and a dropped wrapper class is freed."""

import gc
import weakref

from tpy import Deref, Ptr  # noqa: F401 -- resolves the postponed annotation below


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


def test_declared_ptr_result_is_resolved_not_spelled() -> None:
    """`deref` / `@auto_readonly` hand back the object behind a returned
    pointer proxy unless the result is declared a `Ptr`, however spelled."""
    import tpy
    from tpy import Ptr as P, auto_readonly, deref, take_ptr
    from tpy import _declares_ptr

    def alias() -> P[Point]: ...
    def postponed() -> "Ptr[Point]": ...
    def optional() -> P[Point] | None: ...
    def ro() -> tpy.readonly[P[Point]]: ...
    def plain() -> Point: ...
    assert _declares_ptr(alias) and _declares_ptr(postponed)
    assert _declares_ptr(optional) and _declares_ptr(ro)
    assert not _declares_ptr(plain)

    class Handle:
        def __init__(self, p: Point) -> None:
            self.ptr = take_ptr(p)

        def __deref__(self) -> Point:
            return self.ptr

        @auto_readonly
        def get(self) -> P[Point]:
            return self.ptr

        @auto_readonly
        def obj(self) -> Point:
            return self.ptr

    pt = Point(3)
    h = Handle(pt)
    assert deref(h) is pt
    assert h.obj() is pt
    assert h.get() is not pt and deref(h.get()) is pt


class _GenericHandle[T]:
    def __init__(self, p: "Ptr[T]") -> None:
        self.p = p

    def get(self) -> "Ptr[T]":
        return self.p

    def obj(self) -> "T":
        return self.p


def test_postponed_generic_ptr_result_keeps_the_pointer() -> None:
    """A postponed `-> Ptr[T]` naming a class type parameter is still a
    `Ptr`: the unresolvable `T` must not make the module's real `Ptr`
    unresolvable too."""
    from tpy import _declares_ptr, auto_readonly, deref, take_ptr

    assert _declares_ptr(_GenericHandle.get)
    assert not _declares_ptr(_GenericHandle.obj)
    get = auto_readonly(_GenericHandle.get)
    pt = Point(1)
    h = _GenericHandle(take_ptr(pt))
    q = get(h)
    deref(q).x = 9
    assert pt.x == 9


_FUTURE_MODULE = '''
from __future__ import annotations
from tpy import Ptr, Ptr as P, auto_readonly, take_ptr


class Point:
    def __init__(self, x: int) -> None:
        self.x = x


class Handle[T]:
    def __init__(self, p: Ptr[T]) -> None:
        self.p = p

    @auto_readonly
    def quoted(self) -> "Ptr[T]":
        return self.p

    @auto_readonly
    def alias(self) -> P[T]:
        return self.p

    @auto_readonly
    def optional(self) -> Ptr[T] | None:
        return self.p

    @auto_readonly
    def obj(self) -> T:
        return self.p

    @auto_readonly
    def unrelated_param(self, unused: Later[int] | None = None) -> Ptr[T]:
        return self.p

    @auto_readonly
    def pep695_alias(self) -> PP:
        return self.p


type PP = Ptr[Point]
'''


def test_postponed_annotations_resolve_under_future_import() -> None:
    """Under `from __future__ import annotations` every result annotation is
    a string: a quoted one inside it, an alias and `| None` still name a
    `Ptr`, and a class type parameter resolves from the class's scope."""
    import types
    from tpy import deref

    mod = types.ModuleType("_cpy_stub_future")
    exec(compile(_FUTURE_MODULE, "_cpy_stub_future", "exec"), mod.__dict__)
    pt = mod.Point(1)
    h = mod.Handle(mod.take_ptr(pt))
    for name in ("quoted", "alias", "optional", "unrelated_param",
                 "pep695_alias"):
        q = getattr(h, name)()
        assert q is not pt, name
        deref(q).x += 1
    assert pt.x == 6
    assert h.obj() is pt
