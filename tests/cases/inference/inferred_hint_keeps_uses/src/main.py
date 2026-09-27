# An inferred local's type still hints a rebind (literal width, lambda params, T,
# a nested []) without converting ints; a declared float slot still converts.
import asyncio
import math
from typing import Callable, Iterator

from tpy import Own, dispatch, float32, int32, int64


G: float = 0.5


def empty_list[T](n: int32) -> Own[list[T]]:
    out: list[T] = []
    return out


class Maker:
    def make_list[T](self, n: int32) -> Own[list[T]]:
        out: list[T] = []
        return out


class Bag[T]:
    xs: list[T]

    def __init__(self) -> None:
        self.xs = []


@dispatch
def tot3(d: dict[str, float]) -> float:
    return 1.5 + len(d)


@dispatch
def tot3(s: str) -> str:
    return s


@dispatch
def tots(s: set[float]) -> float:
    return 1.5 + len(s)


@dispatch
def tots(s: str) -> str:
    return s


@dispatch
def keep(v: float) -> float:
    return v


@dispatch
def keep(s: str) -> str:
    return s


def call0[T](f: Callable[[], T]) -> T:
    return f()


@dispatch
def usebag(b: Bag[float]) -> float:
    return 1.5 + len(b.xs)


@dispatch
def usebag(s: str) -> str:
    return s


class Bag2[T]:
    pass


@dispatch
def ub(b: Bag2[float]) -> float:
    return 1.5


@dispatch
def ub(s: str) -> str:
    return s


class Rec:
    def __init__(self, v: int32) -> None:
        self.v = v


def peek[T](r: Rec, xs: Own[list[T]]) -> Own[list[T]]:
    r.v += 1
    return xs


@dispatch
def f32d(d: dict[str, float32]) -> float32:
    return d["k"]


@dispatch
def f32d(s: str) -> str:
    return s


def take_float(x: float) -> float:
    return x


def tuple_t_float[T](p: tuple[T, float]) -> T:
    print("tuple_t_float_param", p[1] / 2)
    return p[0]


def apply_t[T](fn: Callable[[T], float], x: T) -> T:
    print("callable_t_float_param", fn(x) / 2)
    return x


@dispatch
def twice(v: float) -> float:
    return v * 2.0


@dispatch
def twice(v: str) -> str:
    return v


@dispatch
def tot2(xs: list[float]) -> float:
    return 1.5 if len(xs) == 0 else xs[0]


@dispatch
def tot2(s: str) -> str:
    return s


@dispatch
def width_h(v: float32) -> float32:
    return v


@dispatch
def width_h(v: float) -> float:
    return v


@dispatch
def width_g(v: int64) -> int64:
    return v


@dispatch
def width_g(v: int32) -> int32:
    return v


def pick2[T](v: list[T], f: float) -> T:
    print("pick2_param", f / 2)
    return v[0]


def ret_float(a: int32, c: bool) -> float:
    # A declared `-> float` return converts the int arm.
    return a if c else 2.5  # tpyc: ok


def declared_slots(ints: list[int32], a: int32) -> None:
    # An annotated local converts the comprehension's ints.
    ys: list[float] = [i for i in ints]  # tpyc: ok
    print("annotated", sum(ys) / 2)
    # A float parameter converts the int argument.
    print("param", take_float(a) / 2)
    print("return", ret_float(a, True) / 2)
    x: float = 0.5
    # A rebind of an annotated local converts.
    x = a  # tpyc: ok
    print("annotated_rebind", x / 2)


def nonlocal_slot(a: int32) -> None:
    x: float = 0.5

    def set_it() -> None:
        nonlocal x
        # The nonlocal write reaches the annotated declaration.
        x = a  # tpyc: ok

    set_it()
    print("nonlocal", x / 2)


def nested_declared(xs: list[float], a: int32) -> None:
    ys = xs
    # The inferred hint ends at the call: its declared float parameter converts.
    ys = [take_float(a)]  # tpyc: ok
    print("nested_declared", ys[0] / 2)


def float32_width() -> None:
    e = {"a": float32(0.25)}
    # The inferred dict[str, float32] still narrows the float literal.
    e = {"k": 0.5}  # tpyc: ok
    print("float32_width", e["k"])


def lambda_params(h: Callable[[float], float]) -> None:
    k = h
    # The inferred Callable still types the lambda's parameter.
    k = lambda x: x + 1.0  # tpyc: ok
    print("lambda_params", k(1.0))


def generic_t(xs: list[float]) -> None:
    ys = xs
    # The inferred list[float] still picks the generic call's T.
    ys = empty_list(3)  # tpyc: ok
    ys.append(2.5)
    print("generic_t", ys)


def nested_empty(xss: list[list[float]]) -> None:
    zs = xss
    # The inferred element type still gives the nested [] its elements.
    zs = [[], [2.5]]  # tpyc: ok
    zs[0].append(1.5)
    print("nested_empty", zs)


def float_values(xs: list[float], fs: list[float]) -> None:
    ys = xs
    # A comprehension of floats rebinds a float list.
    ys = [f * 2.0 for f in fs]  # tpyc: ok
    print("float_comp", ys)
    x = 0.5
    # round with ndigits returns a float.
    x = round(2.7, 1)  # tpyc: ok
    print("round_ndigits", x)


class Holder:
    def rebind(self, xs: list[float], fs: list[float]) -> None:
        ys = xs
        # Method body: the float comprehension rebind.
        ys = [f + 1.0 for f in fs]  # tpyc: ok
        print("method", ys)


def int_hint_round(v: float) -> None:
    n = int64(0)
    # An int local's type still picks round's result type: no int32 overflow.
    n = round(v)  # tpyc: ok
    print("int_hint_round", n)


def generic_method(xs: list[float]) -> None:
    ys = xs
    # The inferred list[float] still picks a generic METHOD's T.
    ys = Maker().make_list(3)  # tpyc: ok
    ys.append(2.5)
    print("generic_method", ys)


def global_slot(a: int32) -> None:
    global G
    # The global write reaches the annotated module-level declaration.
    G = a  # tpyc: ok
    print("global", G / 2)


def gen_body(fs: list[float]) -> Iterator[float]:
    e = {"a": float32(0.25)}
    # Generator body: the inferred dict[str, float32] still narrows the literal.
    e = {"k": 0.5}  # tpyc: ok
    yield e["k"] + fs[0]


async def async_body(fs: list[float]) -> float:
    e = {"a": float32(0.25)}
    # Async body: the same narrowing across an await-free coroutine.
    e = {"k": 0.5}  # tpyc: ok
    return e["k"] + fs[0]


def structural(a: int32, c: bool, h: Callable[[], list[float]]) -> None:
    y = 0
    # Only T in tuple[T, float] is the local's: the declared float converts a.
    y = tuple_t_float((1, a if c else 2.5))  # tpyc: ok
    print("tuple_t_float", y)
    z = 0.5
    # Only T in Callable[[T], float] is the local's: the lambda's -> float converts 1.
    z = apply_t(lambda w: 1, 2.5)  # tpyc: ok
    print("callable_t_float", z)
    x = 0.5
    # A declared float param of an OVERLOADED function still converts a.
    x = twice(a)  # tpyc: ok
    print("overload_param", x / 2)
    q = 0.5
    # pick2's T is the local's, its declared f converts a.
    q = pick2([2.5], a)  # tpyc: ok
    print("pick2", q)
    k = h
    # An Fn-hinted lambda returning a float list literal.
    k = lambda: [2.5]  # tpyc: ok
    print("fn_list", k())


def int_width(c: bool, v: float, n: int32) -> None:
    m = int64(0) if c else None
    # An int-or-None local still picks round's result type.
    m = round(v)  # tpyc: ok
    print("opt_int_round", m)
    p = int64(0)
    # An int64 local rebound to pow of int32 values.
    p = pow(n, 2)  # tpyc: ok
    print("pow", p)
    d = (int64(0), int64(0))
    # An int64 tuple local rebound to divmod's int pair.
    d = divmod(n, 3)  # tpyc: ok
    print("divmod", d)
    s = int64(0)
    # An int64 local rebound to sum of an int list.
    s = sum([n, 2])  # tpyc: ok
    print("sum", s)
    r = int64(0)
    # An int64 local rebound to math.prod of an int list.
    r = math.prod([n, 2])  # tpyc: ok
    print("prod", r)
    t = int64(0)
    # An int64 local still picks the outer round's result type.
    t = round(round(v))  # tpyc: ok
    print("nested_round", t)


def overload_args_pick(n: int32) -> None:
    y = 0.5
    # tot2's float-list overload is the one candidate matching the local; its
    # declared type fills empty_list's T, which nothing else binds.
    y = tot2(empty_list(3))  # tpyc: ok
    print("args_pick_nested", y)
    x = float32(0.5)
    # A float literal argument under a float32 local; which width_h overload
    # it picks follows their declaration order (BUGS.md#dispatch-float-width-order).
    x = width_h(0.25)  # tpyc: ok
    print("args_pick_float32", x)
    w = int64(0)
    # The literal's width, not the inferred int64 local, picks the overload.
    w = width_g(3000000000)  # tpyc: ok
    print("args_pick_int64", w)
    g = 0.5
    # A generator-expression argument to an overloaded call (the snapshot
    # keeps a dead frame, BUGS.md#dispatch-arg-genexpr-reanalysis).
    g = twice(sum(v * 1.0 for v in [n, 2]))  # tpyc: ok
    print("args_pick_genexpr", g)


def overload_fills() -> None:
    y = 0.5
    # The one overload matching the local types the empty dict's elements.
    y = tot3({})  # tpyc: ok
    print("fill_empty_dict", y)
    # ... the empty set's elements.
    y = tots(set())  # tpyc: ok
    print("fill_empty_set", y)
    # ... the T of a generic call typing a lambda inside the argument.
    y = keep(call0(lambda: 2.5))  # tpyc: ok
    print("fill_lambda_generic", y)
    # ... but converts none of the lambda's ints: call0 returns the int,
    # which keep's declared float parameter converts.
    y = keep(call0(lambda: 2))  # tpyc: ok
    # y is the float 2.0 where CPython keeps the int 2 (a documented declared
    # slot conversion); printing y / 2 does not show that difference.
    print("fill_lambda_int_return", y / 2)
    # ... a generic record construction's T, which its __init__ does not bind.
    y = usebag(Bag())  # tpyc: ok
    print("fill_record_ctor", y)
    # ... the T of a generic record with no __init__.
    y = ub(Bag2())  # tpyc: ok
    print("fill_record_no_init", y)
    r = Rec(1)
    # ... the T of a generic call whose own argument is a generic call: the
    # inner call's T is seeded from it; peek mutates the borrowed r.
    y = tot2(peek(r, empty_list(3)))  # tpyc: ok
    print("fill_nested_generic", y, r.v)
    # ... a generic METHOD's return-only T.
    y = tot2(Maker().make_list(3))  # tpyc: ok
    print("fill_generic_method", y)
    # ... an overloaded call's own argument: the fill aimed at it is its
    # LHS too, so its one matching overload types the empty dict.
    y = keep(tot3({}))  # tpyc: ok
    print("fill_nested_overload", y)
    z = float32(0.5)
    # ... the width of a float literal in a dict literal.
    z = f32d({"k": 0.25})  # tpyc: ok
    print("fill_float32_width", z)


def pack(*xs: int) -> Own[list[int]]:
    return [x for x in xs]


class PackUser:
    xs: list[int]

    def __init__(self) -> None:
        self.xs = [1, 2]

    def run(self) -> None:
        kn = len(self.xs)
        # A varargs call as the argument of an overloaded builtin (len).
        kn = len(pack(1, 2, 3))  # tpyc: ok
        print("pack_arg", kn)


def main() -> None:
    structural(3, True, lambda: [0.5])
    int_width(True, 7.6, 7)
    overload_args_pick(7)
    overload_fills()
    PackUser().run()
    declared_slots([3], 3)
    nonlocal_slot(3)
    nested_declared([1.5], 3)
    float32_width()
    lambda_params(lambda x: x)
    generic_t([1.5])
    nested_empty([[1.5]])
    float_values([1.5], [2.5])
    Holder().rebind([1.5], [0.5])
    int_hint_round(1e12)
    generic_method([1.5])
    global_slot(3)
    for v in gen_body([1.5]):
        print("generator", v)
    print("async", asyncio.run(async_body([1.5])))


main()
# Module level: the inferred dict[str, float32] still narrows the literal.
mod_e = {"a": float32(0.25)}
mod_e = {"k": 0.5}  # tpyc: ok
print("module", mod_e["k"])
