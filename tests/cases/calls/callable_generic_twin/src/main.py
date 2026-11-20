# A generic called with a callable binds the callable's DECLARED form: a
# borrow (`identity -> P`) lends through it, a fresh value (`mk -> Own[P]`,
# a lambda building one) is handed over -- as the hand-written twin would.
from typing import Callable, Iterable, Iterator
from tpy import Fn, int32
from fixtures import P, identity, mk
import part_bind
import part_bounds
import part_class_args
import part_frames
import part_frames_er
import part_iter
import part_paths
import part_red
import part_reduce


def app[A, R](f: Fn[[A], R], a: A) -> R:
    return f(a)


def pick[T, U](f: Callable[[T], U], x: T) -> U:
    return f(x)


def pick_cb[T, U](f: Callable[[T], U], x: T) -> Callable[[T], U]:
    return f


def fill[T, U](f: Fn[[T], U], x: T, out: list[U]) -> None:
    out.append(f(x))  # tpyc: warning(/may copy U into owned storage/)


def main() -> None:
    # free function, a lending callable: R = P, the result is `p` itself
    p = P(1)
    r = app(identity, p)  # tpyc: ok
    r.n += 10
    print("app-lend", p.n)
    # free function, an owning callable: R = Own[P], a new value
    q = P(2)
    r2 = app(mk, q)  # tpyc: ok
    r2.n += 10
    print("app-own", q.n, r2.n)
    # a lambda building a value binds R = Own[P]
    r3 = app(lambda x: P(x.n * 3), q)  # tpyc: ok
    r3.n += 1
    print("app-lambda", q.n, r3.n)
    # a Callable parameter: U = P, the call lends
    b = P(5)
    pick(identity, b).n += 1  # tpyc: ok
    print("pick", b.n)
    # a Callable value returned at a generic result keeps lending
    h = pick_cb(identity, b)  # tpyc: ok
    h(b).n += 7
    print("pick-cb", b.n)
    # a lending Callable VALUE at the same slot lends as well
    g: Callable[[P], P] = identity
    h2 = pick_cb(g, b)  # tpyc: ok
    h2(b).n += 7
    print("pick-cb-value", b.n)
    # an owning callable into a list of its result: the new value moves in
    out: list[P] = []
    fill(mk, b, out)  # tpyc: ok
    out[0].n += 100
    print("fill-own", b.n, out[0].n)
    part_iter.main()
    part_red.main()
    part_reduce.main()
    part_class_args.main()
    part_bind.main()
    part_bounds.main()
    part_paths.main()
    part_frames.main()
    part_frames_er.main()


main()
