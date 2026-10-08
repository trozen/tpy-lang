# A reference value a generic hands to a callable's bare-T slot may be written by it: the
# container it came from is a mutable borrow, and the write is visible to the caller.
from functools import reduce
from typing import Callable, Iterable, Iterator
from tpy import int32, Fn, Own, copy


class R:
    def __init__(self, n: int32) -> None:
        self.n = n


class P:
    def __init__(self, x: int32) -> None:
        self.x = x


def red[T](func: Fn[[T, T], T], a: list[T]) -> Own[T]:
    acc = copy(a[0])
    for i in range(1, len(a)):
        acc = func(acc, a[i])
    return acc


def app[T, U](f: Fn[[T], U], xs: list[T]) -> U:
    return f(xs[0])


def each[T](f: Fn[[T], None], xs: list[T]) -> None:
    for x in xs:
        f(x)


def on_values[K, V](f: Fn[[V], None], d: dict[K, V]) -> None:
    for k in d:
        f(d[k])


def on_iter[T](f: Fn[[T], None], it: Iterable[T]) -> None:
    for x in it:
        f(x)


def gen[T](f: Fn[[T], None], xs: list[T]) -> Iterator[int32]:
    for i in range(len(xs)):
        f(xs[i])
        yield i


def inner[T](f: Fn[[T], None], xs: list[T]) -> None:
    f(xs[0])


def outer[T](f: Fn[[T], None], xs: list[T]) -> None:
    inner(f, xs)


def via_local[T](xs: list[T], f: Fn[[T], None]) -> None:
    g: Callable[[T], None] = f
    g(xs[0])


class Runner[T]:
    cbs: list[Callable[[T], None]]

    def __init__(self, cb: Callable[[T], None]) -> None:
        self.cb = cb
        self.cbs = [cb]

    def run(self, f: Fn[[T], None], xs: list[T]) -> None:
        f(xs[0])

    def invoke(self, x: T) -> None:
        self.cb(x)

    def invoke_at(self, x: T) -> None:
        self.cbs[0](x)


def combine(a: R, b: R) -> Own[R]:
    b.n += 10
    return R(a.n + b.n)


def bump(r: R) -> None:
    r.n += 100


def mk() -> Own[list[R]]:
    return [R(1), R(2)]


def add(a: int32, b: int32) -> int32:
    return a + b


def main() -> None:
    rs = [R(1), R(2), R(3)]
    # lambda: reads its params; the element it gets is a mutable R&
    print("lambda", red(lambda a, b: R(a.n + b.n), rs).n)  # tpyc: ok
    # writing callback: the write to each element is visible afterwards
    print("writes", red(combine, rs).n, rs[1].n, rs[2].n)  # tpyc: ok
    # one element through a generic with two type params
    print("app", app(lambda r: r.n, rs))  # tpyc: ok
    # a list of lists: the lambda takes the row as a mutable vector
    rows = [[P(7)], [P(8)]]
    print("rows", app(lambda d: d[0].x, rows))  # tpyc: ok
    # a loop variable over the list handed to the callable
    each(bump, rs)  # tpyc: ok
    print("each", rs[0].n, rs[1].n, rs[2].n)
    # dict values and an Iterable source
    d = {"a": R(1)}
    on_values(bump, d)  # tpyc: ok
    on_iter(bump, rs)  # tpyc: ok
    print("sources", d["a"].n, rs[0].n)
    # generator body, nested forwarder, Callable local
    for i in gen(bump, rs):  # tpyc: ok
        print("gen", i, rs[i].n)
    outer(bump, rs)  # tpyc: ok
    via_local(rs, bump)  # tpyc: ok
    print("forward", rs[0].n)
    # generic method: a Fn param, a Callable field, an indexed Callable field
    run = Runner[R](bump)
    run.run(bump, rs)  # tpyc: ok
    run.invoke(rs[1])  # tpyc: ok
    run.invoke_at(rs[2])  # tpyc: ok
    print("method", rs[0].n, rs[1].n, rs[2].n)
    # functools.reduce over class instances, from a call result
    print("reduce", reduce(lambda a, b: R(a.n + b.n), mk()).n)  # tpyc: ok
    # value-type T: a literal and a call result still bind (a named temp)
    print("ints", reduce(add, [1, 2, 3]), red(add, [4, 5]))  # tpyc: ok
    # forwarding the list being iterated warns: the resize mark of the bare-T slot
    # lands on the whole list, a false positive here
    # (BUGS.md#callable-arg-structure-effect-depth)
    vs = [1, 2]
    for v in vs:
        print("loop", v, reduce(add, vs))  # tpyc: warning(/may invalidate references/)
    for r in rs:
        print("loop-r", r.n, red(combine, rs).n)  # tpyc: warning(/may invalidate references/)


main()
