# Omitting BOTH a positional-or-keyword default and the keyword-only defaults
# after it must bind each its own default. TPy models defaults as C++
# positional defaults, so the keyword-only default used to slide into the
# skipped positional's slot (b saw c's 20). The sections in `sections()`
# bind keyword arguments once per call, the way CPython does;
# `sections_reentry()` binds them again from the call as written each time
# a call is analyzed through a proxy or a second time.
from typing import TypedDict, Unpack

from tpy import Fn, dispatch, int64

import kwmod


def combine(a: int64, b: int64 = 10, *, c: int64 = 20) -> int64:
    return a * 10000 + b * 100 + c


def partial(a: int64, b: int64 = 10, c: int64 = 30, *, d: int64 = 40) -> int64:
    return a * 1000000 + b * 10000 + c * 100 + d


# --- One argument binding per call: each section binds its keyword or
# keyword-only arguments the way CPython does, also when the call is
# analyzed again (an overload winner's argument, a lambda tried per
# candidate, a pending method dispatched again).


# generic *args: the keyword-only parameter after the pack is no element.
def total[T](*xs: T, k: int64 = 5) -> int64:
    return len(xs) * 100 + k


def total2[T](*xs: T, k: str = "") -> str:
    return str(len(xs)) + k


# pending generic method: the receiver's type argument comes from the call,
# which then dispatches again on the resolved receiver.
class Bag[T]:
    def __init__(self) -> None:
        pass

    def put(self, v: T, *, n: int64 = 2) -> None:
        print("pending", v, n)


# generic method called by keyword: inference reads the keyword argument.
class Gen:
    def pick[T](self, x: T, y: int64 = 7) -> T:
        return x


# re-analysis: a keyword-only call that is an overloaded call's argument.
def g(a: int64, *, k: int64 = 5) -> int64:
    return a * 10 + k


@dispatch
def outer(x: int64) -> int64:
    return x


@dispatch
def outer(x: str) -> int64:
    return 0


@dispatch
def apply[U](f: Fn[[int64], U], x: int64) -> U:
    return f(x)


@dispatch
def apply[U](f: Fn[[str], U], x: str) -> U:
    return f(x)


# method with `**kw: Unpack[TD]` beside a regular keyword parameter.
class Opts(TypedDict):
    x: int64


class M:
    def m(self, a: int64 = 0, **kw: Unpack[Opts]) -> int64:
        return a * 10 + kw["x"]


# `Base.m(self, ...)`: the written `self` is the receiver, also when the call
# is analyzed again as an overloaded call's argument.
class Base:
    def m(self, x: int64, *, k: int64 = 4) -> int64:
        return x * 10 + k


class Child(Base):
    def run(self) -> int64:
        return outer(Base.m(self, 3, k=1))  # tpyc: ok


def kw_sum(**kw: Unpack[Opts]) -> int64:
    return kw["x"]


# A filled default of a generic class's method passes the check any argument
# at its slot passes (`0` into `int64 | None`).
class Opt[T]:
    def __init__(self) -> None:
        pass

    def f(self, x: int64 | None = 0, *, k: int64 = 2) -> None:
        print("method-optional-default", x, k)


# Keyword-only arguments are inference evidence, as positional ones are: an
# argument that agrees with `x` infers `T`, and one alone decides it.
def kw_infer[T](x: T, *, k: T = 1) -> T:
    return k


def kw_only[T](*, k: T) -> T:
    return k


class KwPick:
    def pick[T](self, x: T, *, k: T) -> T:
        return k


def kw_after_args[T](*xs: T, k: T) -> T:
    return k


# A `None` at a `T | None` parameter fills its None arm: no evidence for `T`
# beside an argument that is, keyword or positional.
def opt[T](x: T, *, k: T | None = None) -> T:
    if k is not None:
        return k
    return x


def optp[T](x: T, k: T | None = None) -> T:
    if k is not None:
        return k
    return x


class OptPick:
    def pick[T](self, x: T, *, k: T | None = None) -> T:
        return x


class OptBox[T]:
    def __init__(self, x: T, *, k: T | None = None) -> None:
        self.x = x


# A candidate tried per lambda infers `T` from its keyword-only `k`.
@dispatch
def fk[T](*, k: T, fn: Fn[[int64], int64]) -> T:
    return k


@dispatch
def fk(*, k: str, fn: Fn[[str], str]) -> str:
    return k


# The partial inference that types the lambda leaves `T` to `value`, not to
# the `None`.
def apply_opt[T](maybe: T | None, value: T, fn: Fn[[T], T]) -> T:
    return fn(value)


def none_arm_and_trials() -> None:
    print("none-arm-kw", opt(1, k=None))  # tpyc: ok
    print("none-arm-omitted", opt("a"))  # tpyc: ok
    print("none-arm-pos", optp(1, None))  # tpyc: ok
    print("none-arm-method", OptPick().pick(1, k=None))  # tpyc: ok
    print("none-arm-ctor", OptBox(1, k=None).x)  # tpyc: ok
    print("trial-kwonly", fk(k=3, fn=lambda x: x + 1))  # tpyc: ok
    print("none-arm-lambda", apply_opt(None, 3, lambda x: x + 1))  # tpyc: ok


# Re-entry through each proxy that builds a call of its own: an object's
# `__call__`, a module function, a dotted constructor.
class Scaler:
    def __call__(self, a: int64, *, k: int64 = 5) -> int64:
        return a * 10 + k


class Outer:
    class Inner:
        v: int64

        def __init__(self, a: int64, k: int64 = 5) -> None:
            self.v = a * 10 + k


@dispatch
def describe(x: Outer.Inner) -> int64:
    return x.v


@dispatch
def describe(x: str) -> int64:
    return 0


class Coded(Exception):
    def __init__(self, msg: str, code: int64 = 0) -> None:
        super().__init__(msg)
        self.code = code


def raise_coded(n: int64) -> None:
    # A keyword raise constructs the exception as the call `Coded(...)` does.
    raise Coded("coded", code=n)


class Calc:
    def scale(self, f: float | None = None) -> float:
        return f if f is not None else 0.0


@dispatch
def outer_f(x: float) -> float:
    return x + 0.5


@dispatch
def outer_f(x: str) -> float:
    return 0.0


def h(x: int64, k: int64 = 5) -> int64:
    return x * 10 + k


def ht(t: tuple[int64, int64]) -> int64:
    return t[0] * 10 + t[1]


@dispatch
def outer_seq(x: list[int64]) -> int64:
    return x[0] * 10 + len(x)


@dispatch
def outer_seq(x: str) -> int64:
    return 0


def neg(v: int64) -> int64:
    return -v


def builtin_keywords() -> None:
    xs: list[int64] = [3, 1, 2]
    # A builtin's `key=` / `reverse=` binds by name also when the call is
    # analyzed again as `outer`'s argument.
    r = outer_seq(sorted(xs, reverse=True))  # tpyc: ok
    print("builtin-reverse", r)
    k = outer_seq(sorted(xs, key=neg))  # tpyc: ok
    print("builtin-key", k)
    m = outer(min(4, 7, key=neg))  # tpyc: ok
    print("builtin-min-key", m)


def td_x(o: Opts) -> int64:
    return o["x"]


class Disp:
    def __init__(self) -> None:
        pass

    @dispatch
    def m(self, x: int64, k: int64) -> int64:
        return x * 10 + k

    @dispatch
    def m(self, x: str, k: int64) -> int64:
        return k


def callee_reentry() -> None:
    d = Disp()
    # An overloaded method ranked on its keyword `k`, analyzed again.
    print("dispatch-method-outer", outer(d.m(2, k=3)))  # tpyc: ok
    dl: int64 = apply(lambda a: d.m(a, k=3), 2)  # tpyc: ok
    print("dispatch-method-lambda", dl)
    # A TypedDict construction stays keyword-only when analyzed again.
    print("td-outer", outer(td_x(Opts(x=2))))  # tpyc: ok
    tl: int64 = apply(lambda a: td_x(Opts(x=a)), 4)  # tpyc: ok
    print("td-lambda", tl)
    # A constructor overload group (OSError's) resolved again.
    print("ctor-group-outer", outer(OSError(2, "nope").errno))  # tpyc: ok
    gl: int64 = apply(lambda s: len(OSError(2, s).strerror), "abc")  # tpyc: ok
    print("ctor-group-lambda", gl)
    # A native-template constructor resolved again.
    print("ctor-template-outer", outer(int64(2.5)))  # tpyc: ok
    il: int64 = apply(lambda a: int64(a * 2), 4)  # tpyc: ok
    print("ctor-template-lambda", il)


def walrus_reentry(c: bool) -> None:
    q: int64 = 0
    p = 1
    # A walrus holding a pending number is analyzed again as `outer`'s
    # argument, alone and as a select arm.
    w = outer((q := p))  # tpyc: ok
    print("walrus", w, q)
    ws = outer((q := p) if c else 2)  # tpyc: ok
    print("walrus-select", ws, q)


def pending_reentry(c: bool) -> None:
    p = 1
    # A select arm / tuple element holding a pending number is analyzed
    # again as `outer`'s argument, and in a lambda tried per candidate.
    print("pending-select", outer(h(p if c else 2)))  # tpyc: ok
    print("pending-tuple", outer(ht((p, 2))))  # tpyc: ok
    ls: int64 = apply(lambda a: h(p if a > 0 else 2), 3)  # tpyc: ok
    print("pending-lambda-select", ls)
    lt: int64 = apply(lambda a: ht((p, a)), 3)  # tpyc: ok
    print("pending-lambda-tuple", lt)
    walrus_reentry(c)
    builtin_keywords()
    callee_reentry()


def sections() -> None:
    # The `*xs` region ends at the keyword-only `k`, left to its default.
    print("varargs", total(1, 2))  # tpyc: ok
    print("varargs-kw", total2(1, 2, k="seven"))  # tpyc: ok
    b = Bag()
    # Dispatched again on `Bag[int]`, the keyword-only `n` still defaulted.
    b.put(3)  # tpyc: ok
    print("method-kw", Gen().pick(x=3))  # tpyc: ok
    # The keyword-only call binds again when `outer` analyzes its argument.
    print("reanalysis", outer(g(1, k=2)))  # tpyc: ok
    print("reanalysis-varargs", outer(total(1, 2, k=3)))  # tpyc: ok
    # The lambda's body is analyzed once per candidate of `apply`.
    lt: int64 = apply(lambda a: g(a, k=2), 3)  # tpyc: ok
    print("lambda-trial", lt)
    # `a` binds the regular parameter, `x` the TypedDict field.
    print("method-td", M().m(a=1, x=2))  # tpyc: ok
    print("proxy", Child().run())
    d = Opts(x=3)
    print("kwargs-spread", kw_sum(**d))  # tpyc: ok
    sections_reentry()


def sections_reentry() -> None:
    # The omitted `x` is filled with `0` and checked as an argument.
    Opt[int64]().f()  # tpyc: ok
    # The keyword-only `k` is evidence for `T` beside `x`, or alone.
    print("kwonly-infer-free", kw_infer(2.0, k=3.5))  # tpyc: ok
    print("kwonly-infer-alone", kw_only(k=3))  # tpyc: ok
    print("kwonly-infer-method", KwPick().pick(2.0, k=3.5))  # tpyc: ok
    print("kwonly-infer-after-args", kw_after_args(2.0, 1.5, k=3.5))  # tpyc: ok
    s = Scaler()
    # Each proxy call is an overloaded call's argument, so analyzed twice.
    print("call-kw", outer(s(1, k=2)))  # tpyc: ok
    print("module-kw", outer(kwmod.scaled(1, k=3)))  # tpyc: ok
    print("dotted-ctor-kw", describe(Outer.Inner(a=1, k=4)))  # tpyc: ok
    for n in range(2):
        try:
            raise_coded(n + 6)
        except Coded as e:
            print("raise-kw", e.code)
    b = Bag()
    # The pending method's `T` comes from the keyword argument.
    b.put(v=3)  # tpyc: ok
    # An int literal coerced into an Optional float, analyzed again.
    print("reanalysis-coerce", outer_f(Calc().scale(2)))  # tpyc: ok
    ys = []
    ys.append(1.5)
    # A pending list's method argument coerced (2 -> 2.0), analyzed again.
    print("reanalysis-pending", outer(ys.count(2)))  # tpyc: ok
    pending_reentry(True)
    none_arm_and_trials()


def main() -> None:
    print(combine(1))
    print(combine(1, 2))
    print(combine(1, c=3))
    print(combine(1, 2, c=3))

    print(partial(5))
    print(partial(5, d=9))
    print(partial(5, 6, d=9))
    sections()


main()
