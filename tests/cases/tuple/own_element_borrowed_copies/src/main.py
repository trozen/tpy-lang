# A borrowed source at a tuple element's Own[T] slot copies and warns as the
# same value alone does; sections mutate after the boundary to show the copy.
import asyncio
from typing import Iterator, Self
from tpy import int32, Own, copy, error_return, ReturnException


class Big(Exception, ReturnException):
    pass


class P:
    xs: list[int32]

    def __init__(self, x: int32) -> None:
        self.xs = [x]


class Holder:
    a: P
    b: P

    def __init__(self, a: P, b: P) -> None:
        self.a = copy(a)
        self.b = copy(b)

    def first(self) -> P:
        return self.a

    def pair(self) -> tuple[P, int32]:
        return (self.a, 1)

    # a consuming method naming one field twice: the second cannot move
    def give(self: Own[Self]) -> int32:
        return take_both((self.a, self.a))  # tpyc: warning(/argument 't' tuple element 0\)/) warning(/argument 't' tuple element 1\)/)

def mk(p: P) -> tuple[P, int32]:
    return (p, 1)


def take(t: Own[tuple[P, P]]) -> int32:
    a, b = t
    a.xs.append(100)
    return len(a.xs)


def take_both(t: Own[tuple[P, P]]) -> int32:
    a, b = t
    a.xs.append(100)
    return len(a.xs) * 10 + len(b.xs)


def take_opt(t: Own[tuple[P | None, P | None]]) -> int32:
    a, b = t
    if a is not None:
        a.xs.append(100)
        return len(a.xs)
    return 0


def take_each(t: tuple[Own[P], Own[P]]) -> int32:
    a, b = t
    a.xs.append(100)
    return len(a.xs)


def sink(t: tuple[Own[P], int32]) -> int32:
    a, n = t
    a.xs.append(100)
    return len(a.xs)


# return: one element of a returned literal
def ret_literal(p: P) -> tuple[P, Own[P]]:
    return (p, p)  # tpyc: warning(/copies P into owned storage \(tuple element 1\)/)


# return: a whole Own[tuple[...]] literal
def ret_whole(p: P) -> Own[tuple[P, P]]:
    return (p, p)  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)


# return: a tuple local by name at a whole Own[tuple[...]] slot
def ret_whole_name(p: P) -> Own[tuple[P, P]]:
    t = (p, p)
    return t  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)


# return: an element nested in an inner tuple
def ret_nested(p: P) -> tuple[int32, tuple[int32, Own[P]]]:
    return (1, (2, p))  # tpyc: warning(/copies P into owned storage \(tuple element 1\.1\)/)


# return: a borrow-returning call as the member
def ret_call(h: Holder) -> Own[tuple[Own[P], int32]]:
    return (h.first(), 1)  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)


# return: a tuple local by name, and through an alias
def ret_name(p: P) -> tuple[Own[P], int32]:
    pair = (p, 0)
    return pair  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)


def ret_alias(p: P) -> tuple[Own[P], int32]:
    pair = (p, 0)
    pair2 = pair
    return pair2  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)


# return: a tuple local bound from a borrow-returning call
def ret_call_local(p: P) -> tuple[Own[P], int32]:
    pair = mk(p)
    return pair  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)


# return: a tuple parameter by name
def ret_param(t: tuple[P, int32]) -> tuple[Own[P], int32]:
    return t  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)


# return: a loop variable over stored tuples
def ret_loop_var(ps: list[tuple[P, int32]]) -> tuple[Own[P], int32]:
    for pair in ps:
        return pair  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)
    return (P(0), 0)


# return: a tuple local bound on both branches
def ret_branch(p: P, q: P, c: bool) -> tuple[Own[P], int32]:
    if c:
        pair = (p, 0)
    else:
        pair = (q, 1)
    return pair  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)


# return: one owned local at two elements (CPython returns one object twice)
def ret_twice() -> Own[tuple[P, P]]:
    n = P(1)
    return (n, n)  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)


# return: under a finally that mutates the source after the copy
def ret_finally() -> tuple[Own[list[int32]], Own[list[int32]]]:
    ys = [1, 2, 3]
    try:
        return (ys, ys)  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)
    finally:
        ys.append(4)


# return: from a closure, a match arm and an async def
def ret_closure(p: P) -> int32:
    def inner() -> tuple[Own[P], int32]:
        return (p, 0)  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)
    q, _ = inner()
    q.xs.append(8)
    return len(p.xs)


def ret_match(p: P, k: int32) -> tuple[Own[P], int32]:
    match k:
        case 1:
            return (p, 1)  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)
        case _:
            return (P(0), 0)


async def ret_async(p: P) -> tuple[Own[P], int32]:
    return (p, 0)  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)


async def ret_async_name(p: P) -> tuple[Own[P], int32]:
    t = (p, 1)
    return t  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)


# argument: parameters, fields, subscripts and a borrow-returning call as
# literal members
def arg_params(p: P, q: P) -> int32:
    return take((p, q))  # tpyc: warning(/argument 't' tuple element 0\)/) warning(/argument 't' tuple element 1\)/)


def arg_fields(h: Holder) -> int32:
    return take((h.a, h.b))  # tpyc: warning(/argument 't' tuple element 0\)/) warning(/argument 't' tuple element 1\)/)


def arg_subscripts(items: list[P]) -> int32:
    return take((items[0], items[1]))  # tpyc: warning(/argument 't' tuple element 0\)/) warning(/argument 't' tuple element 1\)/)


def arg_fields_optional(h: Holder) -> int32:
    return take_opt((h.a, h.b))  # tpyc: warning(/argument 't' tuple element 0\)/) warning(/argument 't' tuple element 1\)/)


def arg_call(h: Holder) -> int32:
    return take((h.first(), h.b))  # tpyc: warning(/argument 't' tuple element 0\)/) warning(/argument 't' tuple element 1\)/)


# argument: a tuple local passed by name
def arg_name(p: P) -> int32:
    pair = (p, 0)
    return sink(pair)  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)


# a tuple parameter warns once for the whole tuple at an argument
def arg_param_name(t: tuple[P, int32]) -> int32:
    return sink(t)  # tpyc: warning(/copies tuple\[P, int32\] into owned storage/)


def arg_call_local(p: P) -> int32:
    pair = mk(p)
    return sink(pair)  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)


def arg_method_local(h: Holder) -> int32:
    pair = h.pair()
    return sink(pair)  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)


# argument: inside a generator body and an async body, literal and name
def arg_generator(p: P) -> Iterator[int32]:
    yield take((p, p))  # tpyc: warning(/argument 't' tuple element 0\)/) warning(/argument 't' tuple element 1\)/)
    t = (p, 1)
    yield sink(t)  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)


async def arg_async_name(p: P) -> int32:
    t = (p, 1)
    return sink(t)  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)


# argument: a container insert, from a literal, a local and a parameter
def insert_literal(xs: list[tuple[P, P]], p: P) -> None:
    xs.append((p, p))  # tpyc: warning(/argument 'value' tuple element 0\)/) warning(/argument 'value' tuple element 1\)/)


def insert_name(xs: list[tuple[P, P]], p: P) -> None:
    t = (p, p)
    xs.append(t)  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)


def insert_param(xs: list[tuple[P, P]], t: tuple[P, P]) -> None:
    xs.append(t)  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)


def mk2(p: P) -> tuple[P, P]:
    return (p, p)


def insert_call_local(xs: list[tuple[P, P]], p: P) -> None:
    t = mk2(p)
    xs.append(t)  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)


# argument: a method, a constructor and a generic function
class Sink:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def put(self, t: Own[tuple[P, P]]) -> int32:
        a, b = t
        a.xs.append(7)
        return len(a.xs)


class Keeper:
    a: P

    def __init__(self, t: Own[tuple[P, P]]) -> None:
        a, b = t
        # BUGS.md#own-tuple-param-unpack-field-copy: `a` could move here
        self.a = a  # tpyc: warning(/copies P into field/)


def gtake[T](t: Own[tuple[T, T]]) -> int32:
    return 0


# argument: inside an @error_return body and a with body
@error_return(Big)
def arg_error_return(p: P) -> int32:
    if len(p.xs) > 100:
        raise Big
    return take((p, p))  # tpyc: warning(/argument 't' tuple element 0\)/) warning(/argument 't' tuple element 1\)/)


class Cm:
    def __enter__(self) -> int32:
        return 0

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def arg_with(p: P) -> int32:
    with Cm():
        return take((p, p))  # tpyc: warning(/argument 't' tuple element 0\)/) warning(/argument 't' tuple element 1\)/)


async def async_main() -> None:
    a = P(1)
    q, _ = await ret_async(a)
    q.xs.append(9)
    print("ret_async", len(a.xs), len(q.xs))
    q2, _ = await ret_async_name(a)
    q2.xs.append(9)
    print("ret_async_name", len(a.xs), len(q2.xs))
    print("arg_async_name", await arg_async_name(a), len(a.xs))


def main() -> None:
    a = P(1)
    b = P(2)

    s0, s1 = ret_literal(a)
    s1.xs.append(5)
    print("ret_literal", len(a.xs), len(s0.xs), len(s1.xs))
    w0, w1 = ret_whole(a)
    w0.xs.append(5)
    print("ret_whole", len(a.xs), len(w0.xs), len(w1.xs))
    wn0, wn1 = ret_whole_name(a)
    wn0.xs.append(5)
    print("ret_whole_name", len(a.xs), len(wn0.xs), len(wn1.xs))
    n = ret_nested(a)
    a.xs[0] = 50
    print("ret_nested", n[0], n[1][1].xs[0], a.xs[0])
    r0, _ = ret_name(a)
    r0.xs.append(5)
    print("ret_name", len(a.xs), len(r0.xs))
    r1, _ = ret_alias(a)
    r1.xs.append(5)
    print("ret_alias", len(a.xs), len(r1.xs))
    cl, _ = ret_call_local(a)
    cl.xs.append(5)
    print("ret_call_local", len(a.xs), len(cl.xs))
    rp, _ = ret_param((a, 1))
    rp.xs.append(5)
    print("ret_param", len(a.xs), len(rp.xs))
    stored = [(P(3), 1)]
    lv0, _ = ret_loop_var(stored)
    lv0.xs.append(5)
    for st0, _ in stored:
        print("ret_loop_var", len(st0.xs), len(lv0.xs))
    r2, _ = ret_branch(a, b, True)
    r2.xs.append(5)
    print("ret_branch", len(a.xs), len(r2.xs))
    t0, t1 = ret_twice()
    t0.xs.append(5)
    print("ret_twice", len(t0.xs), len(t1.xs))
    y0, y1 = ret_finally()
    y0.append(9)
    print("ret_finally", len(y0), len(y1))
    print("ret_closure", ret_closure(a))
    m0, _ = ret_match(a, 1)
    m0.xs.append(5)
    print("ret_match", len(a.xs), len(m0.xs))
    asyncio.run(async_main())

    # A member not at its last use: the Optional-element and per-element forms.
    print("arg_local", take((a, b)), len(a.xs), len(b.xs))  # tpyc: warning(/argument 't' tuple element 0\)/) warning(/argument 't' tuple element 1\)/)
    print("arg_optional", take_opt((a, b)), len(a.xs))  # tpyc: warning(/argument 't' tuple element 0\)/) warning(/argument 't' tuple element 1\)/)
    print("arg_each", take_each((a, b)), len(a.xs))  # tpyc: warning(/argument 't' tuple element 0\)/) warning(/argument 't' tuple element 1\)/)
    print("arg_params", arg_params(a, b), len(a.xs))
    hd = Holder(a, b)
    print("arg_fields", arg_fields(hd), len(hd.a.xs))
    print("arg_call", arg_call(hd), len(hd.a.xs))
    c0, _ = ret_call(hd)
    c0.xs.append(5)
    print("ret_call", len(hd.a.xs), len(c0.xs))
    zs = [P(5), P(6)]
    print("arg_subscripts", arg_subscripts(zs), len(zs[0].xs))
    print("arg_name", arg_name(a), len(a.xs))
    print("arg_param_name", arg_param_name((a, 1)), len(a.xs))
    print("arg_call_local", arg_call_local(a), len(a.xs))
    print("arg_method_local", arg_method_local(hd), len(hd.a.xs))
    print("arg_fields_optional", arg_fields_optional(hd), len(hd.a.xs))
    # A fresh member beside a borrowed one: only the borrowed one copies.
    print("arg_mixed", take_opt((P(99), a)), len(a.xs))  # tpyc: warning(/argument 't' tuple element 1\)/)
    for g in arg_generator(a):
        print("arg_generator", g, len(a.xs))
    # A double move would leave the second element empty.
    print("give", Holder(a, b).give())

    xs: list[tuple[P, P]] = []
    insert_literal(xs, a)
    insert_name(xs, a)
    insert_param(xs, (a, b))  # tpyc: ok
    insert_call_local(xs, a)
    a.xs.append(42)
    for i0, _ in xs:
        print("insert", len(a.xs), len(i0.xs))

    sk = Sink()
    print("method", sk.put((a, b)), len(a.xs))  # tpyc: warning(/argument 't' tuple element 0\)/) warning(/argument 't' tuple element 1\)/)
    k = Keeper((a, b))  # tpyc: warning(/argument 't' tuple element 0\)/) warning(/argument 't' tuple element 1\)/)
    a.xs.append(6)
    print("ctor", len(k.a.xs), len(a.xs))
    # The callee only reads; a move out of `a` would empty it.
    print("generic", gtake((a, b)), len(a.xs))  # tpyc: warning(/argument 't' tuple element 0\)/) warning(/argument 't' tuple element 1\)/)
    try:
        print("arg_error_return", arg_error_return(a), len(a.xs))
    except Big:
        pass
    print("arg_with", arg_with(a), len(a.xs), len(b.xs))



main()
