# Method and property templates spell their receiver: a side-effecting
# receiver and argument are each evaluated once.
from __future__ import annotations
from tpy import int32, dispatch, Own, readonly, auto_readonly
from tpy.extern import cpp_template, native
from fixtures import mark_i


class Picker:
    def __init__(self) -> None:
        pass

    @dispatch
    @cpp_template("(static_cast<void>({self}), static_cast<void>({0}), 7)")
    def pick(self, v: int32) -> int32: ...  # tpyc: ok

    @dispatch
    def pick(self, v: str) -> int32:
        return 0


def make_picker() -> Own[Picker]:
    print("method receiver evaluated")
    return Picker()


@native("std::vector")
class Vec[T]:
    @property
    @cpp_template("static_cast<int32_t>({self}.size())")
    @readonly
    def count(self) -> int32: ...  # tpyc: ok

    @count.setter
    @cpp_template("{self}.reserve(static_cast<std::size_t>({0}))")
    def count(self, value: int32) -> None: ...  # tpyc: ok

    # Receiverless templates: no `{self}`.
    @staticmethod
    @cpp_template("({0} * 2)")
    def twice(x: int32) -> int32: ...  # tpyc: ok

    @classmethod
    @cpp_template("({0} + 1)")
    def bumped(cls, x: int32) -> int32: ...  # tpyc: ok

    # One template for both clones; each spells the receiver once.
    @auto_readonly
    @cpp_template("(static_cast<void>({0}), {self})")
    def touch(self, arg: int32) -> auto_readonly[Vec[T]]: ...  # tpyc: ok


# A static operator dunder bound by a template: the operator form passes only
# the right operand.
@native("std::string")
class Adder:
    @staticmethod
    @cpp_template("({0} + 2)")
    def __add__(n: int32) -> int32: ...  # tpyc: ok


# A static unary operator dunder bound by a template: no operand at all.
@native("std::string")
class Negater:
    @staticmethod
    @cpp_template("42")
    def __neg__() -> int32: ...  # tpyc: ok


# A plain static dunder called through an instance.
class Halver:
    def __init__(self) -> None:
        pass

    @staticmethod
    def __floordiv__(n: int32) -> int32:
        return n + 2


def make_vec() -> Own[Vec[int32]]:
    print("property receiver evaluated")
    return Vec[int32]()


def const_recv(v: readonly[Vec[int32]]) -> readonly[Vec[int32]]:
    print("readonly receiver evaluated")
    return v


def mut_recv(v: Vec[int32]) -> Vec[int32]:
    print("mutable receiver evaluated")
    return v


def touch_both(r: readonly[Vec[int32]], w: Vec[int32]) -> None:
    # the readonly clone, then the mutable clone: the receiver and the
    # argument are each evaluated once; one side effect per call, since the
    # template spells the argument ahead of the receiver
    const_recv(r).touch(0)  # tpyc: ok
    r.touch(mark_i("readonly touch", 1))  # tpyc: ok
    mut_recv(w).touch(0)  # tpyc: ok
    w.touch(mark_i("mutable touch", 2))  # tpyc: ok


def main() -> None:
    # method template: the receiver and the argument
    n = make_picker().pick(mark_i("method", 1))  # tpyc: ok
    print("method", "done")
    # property template: the receiver
    m = make_vec().count  # tpyc: ok
    v = Vec[int32]()
    v.count = mark_i("setter", 8)  # tpyc: ok
    print("property", "done")
    # static and class method templates called through an instance take no
    # receiver operand; `v` is side-effect-free on purpose, since the dropped
    # receiver is BUGS.md#static-template-call-drops-receiver
    w = v.twice(mark_i("static", 2))  # tpyc: ok
    u = v.bumped(mark_i("classmethod", 3))  # tpyc: ok
    print("static", "done")
    # the same templates called through the class; the class receiver is
    # side-effect-free on purpose (BUGS.md#static-template-call-drops-receiver)
    w2 = Vec[int32].twice(mark_i("static class", 2))  # tpyc: ok
    u2 = Vec[int32].bumped(mark_i("classmethod class", 3))  # tpyc: ok
    print("static class", "done")
    # static operator template: the right operand only; `Adder()` is
    # side-effect-free on purpose, since the dropped left operand is
    # BUGS.md#static-template-call-drops-receiver
    s = Adder() + mark_i("static add", 3)  # tpyc: ok
    print("static add", "done")
    # static unary operator template: no operand; `r` is side-effect-free on
    # purpose (BUGS.md#static-template-call-drops-receiver)
    r = Negater()
    t = -r  # tpyc: ok
    print("static neg", "done")
    # plain static dunder through an instance
    q = Halver().__floordiv__(mark_i("static floordiv", 3))  # tpyc: ok
    print("static floordiv", q)
    # @auto_readonly template: both clones
    tv = Vec[int32]()
    touch_both(tv, tv)
    print("auto_readonly", "done")
