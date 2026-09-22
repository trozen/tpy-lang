# tpy: ext_module
# Optional[T] at the @export boundary: None is the gate, a present value is T's
# own crossing. The `T*` form (a reference class, a container) borrows the live
# payload of an exposed class -- the same write-through alias and identity a
# plain class param/return has -- or points into an owned copy of a container
# (the same copy-in a plain container param is, so a mutation still warns).
# The value form (`std::optional<T>`: scalars, str, bytes, an enum, a value
# class, a tuple) copies as T does. Covers a free function, a method,
# __init__, a property getter + setter, a `= None` and a non-None default,
# the identity and field-view paths through the gate, and an all-None body.
# The ext-only halves (TypeError on a wrong type, the copy residue) live in
# ext_checks.py.
from enum import IntEnum
from typing import Optional

from tpy import int32, Own, ValueType, nocopy, readonly
from tpy.extern import export


@export
class Color(IntEnum):
    RED = 1
    BLUE = 2


@export
class Vec2(ValueType):
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


@export
class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


@export
@nocopy
class Token:
    id: int32

    def __init__(self, id: int32) -> None:
        self.id = id


@export
class Inner:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


@export
class Holder:
    _inner: Inner
    _label: Optional[str]
    _limit: Optional[int32]
    seed: int32
    # getset fields: the value form crosses as an attribute in both
    # directions (None in, None out); the class form is `_`-internal
    note: Optional[str]
    tint: Optional[Color]
    anchor: Optional[Vec2]

    # __init__: the value form with a `= None` default and the `T*` form (a
    # borrowed exposed class: the write through it reaches the caller's object)
    def __init__(self, v: int32, label: Optional[str] = None,
                 seed: Optional[Point] = None) -> None:  # tpyc: ok
        self._inner = Inner(v)
        self._label = label
        self._limit = None
        self.note = None
        self.tint = None
        self.anchor = None
        if seed is None:
            self.seed = 0
        else:
            seed.x += 100
            self.seed = seed.x

    def has_label(self) -> bool:
        return self._label is not None

    # Field borrow through the gate: the present arm crosses as an aliasing
    # borrow view of `_inner`, exactly as `-> Inner` does.
    def inner_if(self, flag: bool) -> Optional[Inner]:  # tpyc: ok
        if flag:
            return self._inner
        return None

    # Identity through the gate: `self` or the param crosses back as the
    # ORIGINAL PyObject; None is None.
    def pick(self, other: Optional["Holder"], mine: bool) -> Optional["Holder"]:  # tpyc: ok
        if mine:
            return self
        return other

    # property: the value form at a getter return and a setter value
    @property
    def limit(self) -> Optional[int32]:  # tpyc: ok
        return self._limit

    @limit.setter
    def limit(self, v: Optional[int32]) -> None:  # tpyc: ok
        # narrowed reads: storing an un-narrowed value Optional whole is a
        # queued lowering reject (name.optval_unproven_read), not a boundary matter
        if v is None:
            self._limit = None
        else:
            self._limit = v


ORIGIN = Point(0)


# free function: scalar value form, both directions
@export
def opt_int(v: Optional[int32]) -> Optional[int32]:  # tpyc: ok
    if v is None:
        return None
    return v + 1


# free function: str / bytes -- the owned optional local converts to the
# callee's view-form param
@export
def opt_str(s: Optional[str]) -> Optional[str]:  # tpyc: ok
    if s is None:
        return None
    # bound first: a view-source `-> str | None` return is a filed reject
    # (BUGS.md#optional-str-return-view-source), not a boundary matter
    out = s + "!"
    return out


@export
def opt_bytes(b: Optional[bytes]) -> Optional[bytes]:  # tpyc: ok
    if b is None:
        return None
    out = b + b"?"
    return out


# free function: exposed enum
@export
def opt_color(c: Optional[Color]) -> Optional[Color]:  # tpyc: ok
    if c is None:
        return Color.RED
    if c == Color.RED:
        return Color.BLUE
    return None


# free function: value class copies in and out
@export
def opt_vec(v: Optional[Vec2]) -> Optional[Vec2]:  # tpyc: ok
    if v is None:
        return None
    return Vec2(v.x * 10, v.y * 10)


# free function: reference class -- the `T*` form; mutation writes through,
# `return p` hands back the caller's own object
@export
def bump(p: Optional[Point], by: Optional[int32] = None) -> Optional[Point]:  # tpyc: ok
    if p is None:
        return None
    p.x += 1 if by is None else by
    return p


# free function: a `= None` default on the reference form
@export
def x_or(p: Optional[Point] = None, fallback: int32 = -1) -> int32:  # tpyc: ok
    if p is None:
        return fallback
    return p.x


# free function: non-None defaults on the value form
@export
def scaled(v: int32, by: Optional[int32] = 3, tag: Optional[str] = "x") -> str:  # tpyc: ok
    s = str(v * (1 if by is None else by))
    if tag is None:
        return s
    return tag + s


# free function: a `= None` default on a value-form container whose element
# is an exposed type (the local is `auto`-declared, the omitted slot too)
@export
def pair_or(t: Optional[tuple[Color, int32]] = None) -> int32:  # tpyc: ok
    if t is None:
        return -1
    return t[1] * (2 if t[0] == Color.BLUE else 1)


# free function: containers -- Optional[list] is a copy-in like list, so a
# mutation warns
@export
def opt_list(xs: Optional[list[int32]]) -> Optional[list[int32]]:  # tpyc: warning(/list parameter 'xs' is copied in/)
    if xs is None:
        return None
    xs.append(len(xs))
    return xs  # tpyc: warning(/returns a list by reference/)


# free function: the `T*` form of a container with a `= None` default (the
# omitted slot spells its type through the marshal call itself)
@export
def count_or(xs: Optional[list[int32]] = None) -> int32:  # tpyc: ok
    if xs is None:
        return -1
    return len(xs)


# free function: the `T*` form of a set -- copied in like a set, so the
# mutation warns
@export
def opt_set(s: Optional[set[int32]]) -> int32:  # tpyc: warning(/set parameter 's' is copied in/)
    if s is None:
        return -1
    s.add(99)
    return len(s)


@export
def opt_dict(d: Optional[dict[str, int32]]) -> int32:  # tpyc: ok
    if d is None:
        return -1
    total = 0
    for _k, v in d.items():
        total += v
    return total


@export
def opt_pair(t: Optional[tuple[int32, str]]) -> Optional[tuple[int32, str]]:  # tpyc: ok
    if t is None:
        return None
    return (t[0] + 1, t[1] + t[1])


# free function: every return is None -- nothing crosses by borrow, so no
# copy warning
@export
def never(p: Point) -> Optional[Point]:  # tpyc: ok
    return None


# free function: a module global has no live object behind it -- the
# present arm copies, and says so
@export
def origin_if(flag: bool) -> Optional[Point]:  # tpyc: ok
    if flag:
        return ORIGIN  # tpyc: warning(/copied across the CPython boundary/)
    return None


# free function: a readonly inner is peeled like any other wrapper -- the
# copy warning still names the class
@export
def origin_ro(flag: bool) -> Optional[readonly[Point]]:  # tpyc: ok
    if flag:
        return ORIGIN  # tpyc: warning(/returns exposed class 'Point' by reference/)
    return None


# free function: Own forces the value form -- a fresh instance either way
@export
def fresh(flag: bool) -> Own[Optional[Point]]:  # tpyc: ok
    if flag:
        return Point(42)
    return None


# free function: the owned instance MOVES out through the gate (a @nocopy
# class would not build if the leaf copied)
@export
def mint(flag: bool) -> Own[Optional[Token]]:  # tpyc: ok
    if flag:
        return Token(7)
    return None
