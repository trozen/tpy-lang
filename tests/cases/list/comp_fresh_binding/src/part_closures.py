# A comprehension variable is no read of the enclosing name: no capture, no
# demotion, no liveness; a true clause-0 or clause-1 capture stays.
from typing import Callable, Iterator, Self
from tpy import Own, int32, nocopy


@nocopy
class B:
    def __init__(self, v: int) -> None:
        self.v = v

    def get(self) -> int:
        return self.v


def take(b: Own[B]) -> int:
    return b.v


def own_gen() -> Iterator[Own[B]]:
    yield B(1)
    yield B(2)


def pair(a: int, rs: list[int]) -> int:
    return a + len(rs)


class P:
    def __init__(self, x: int) -> None:
        self.x = x


class R:
    def __init__(self, v: int) -> None:
        self.v = v


class Bx:
    def __init__(self, n: int) -> None:
        self.n = n

    def finish(self: Own[Self]) -> int:
        return self.n


def ap(f: Callable[[Bx], int], b: Bx) -> int:
    return f(b)


def capture(rows: list[int]) -> None:
    ys = [1, 2]
    h: Callable[[], int] = lambda: len([ys for ys in rows])  # tpyc: warning(/copies the object 'rows'/)
    ys.append(3)
    print("capture", h(), ys)


def nested_def(rows: list[int]) -> None:
    ys = [1, 2]

    def g() -> int:
        return len([ys for ys in rows])  # tpyc: ok
    ys.append(3)
    rows.append(4)
    print("nested_def", g(), ys)


def source_capture(rows: list[int]) -> None:
    f: Callable[[], int] = lambda: len([r for r in rows])  # tpyc: warning(/copies the object 'rows'/)
    print("source_capture", f())


def clause1_capture(rows: list[int], zs: list[int]) -> None:
    def g() -> int:
        return len([z for r in rows for z in zs])  # tpyc: ok
    zs.append(7)
    print("clause1_capture", g())


def clause1_lambda(rows: list[int], zs: list[int]) -> None:
    # The lambda captures the clause-1 iterable zs (copied, warned) as well
    # as clause 0's rows; zs grows after, which the copy does not see.
    f: Callable[[], int] = lambda: len([z for r in rows for z in zs])  # tpyc: warning(/copies the object 'rows'/) warning(/copies the object 'zs'/)
    print("clause1_lambda", f())
    zs.append(7)
    print("clause1_after", zs)


def self_consume() -> None:
    b = [B(1), B(2)]
    # The source is the outer b at its last use, the element the inner b.
    r = [b for b in b]  # tpyc: ok
    r[0].v = 5
    print("self_consume", len(r), r[0].v)


def same_stmt(bs: list[B]) -> None:
    b = B(9)
    # take(b) is the outer b's last use; the comprehension's b is its own.
    print("same_stmt", pair(take(b), [b.get() for b in bs]))  # tpyc: ok


def own_tuple(k: int) -> None:
    b = B(9)
    # Each step moves into its tuple, whatever the outer b's liveness.
    rs = [(b, k) for b in own_gen()]  # tpyc: ok
    b.v += 1
    print("own_tuple", b.v, len(rs), rs[1][0].v)


def lambda_leak(ps: list[P]) -> None:
    a = B(9)
    n = take(a)
    # The lambdas read the element a (copied, warned), not the consumed one.
    fs: list[Callable[[], int]] = [lambda: a.x for a in ps]  # tpyc: warning(/copies the object 'a'/) warning(/bound again on each iteration/)
    ps[0].x = 7
    print("lambda_leak", n, len(fs))


def inner_take() -> None:
    b = B(9)
    # An inner take(b) consumes each step, not the outer b.
    ns = [take(b) for b in own_gen()]  # tpyc: ok
    print("inner_take", ns, b.get())


def loop_move(ys: list[R]) -> None:
    xs = [R(1), R(2)]
    keep: list[R] = []
    for r in xs:
        # The loop moves each element; the comprehension's r below is its own.
        keep.append(r)  # tpyc: ok
    keep[0].v = 5
    print("loop_move", [r.v for r in keep], [r.v for r in ys])


def lambda_param_consumed(ws: list[Bx]) -> None:
    b = Bx(2)
    old = b.finish()
    # The lambda's b is its parameter, not the consumed local.
    n = ap(lambda b: b.n + 1, ws[0])  # tpyc: ok
    ws[0].n += 5
    print("lambda_param_consumed", old, n, ws[0].n)


def head_capture(d: dict[str, int], ps: list[tuple[str, int]],
                 bps: list[tuple[bytes, int]]) -> None:
    # Uncaptured and inert, the str head stays a view of the item...
    print("head_view", [len(k) for k, v in d.items()])  # tpyc: ok
    # ... as does a bytes head.
    print("bytes_head_view", [len(b) for b, v in bps])  # tpyc: ok
    bps.clear()
    # Captured, it owns its key (copied, as the for head's), which the
    # source's clearing after does not reach.
    fs: list[Callable[[], str]] = [lambda: k for k, v in d.items()]  # tpyc: warning(/copies 'k'/) warning(/bound again on each iteration/)
    gs: list[Callable[[], str]] = [lambda: k for k, n in ps]  # tpyc: warning(/copies 'k'/) warning(/bound again on each iteration/)
    d.clear()
    ps.clear()
    print("head_capture", fs[-1](), gs[-1]())


def str_pairs(n: int) -> Iterator[tuple[str, int]]:
    for i in range(n):
        yield ("gen-key-" + str(i) * 30, i)


def head_gen_capture(ps: list[tuple[str, int]]) -> None:
    # A generator's step owns its str element: the captured head owns its
    # copy, which outlives the exhausted generator.
    fs: list[Callable[[], str]] = [lambda: k for k, n in str_pairs(3)]  # tpyc: warning(/copies 'k'/) warning(/bound again on each iteration/)
    junk = ["z" * 60 for i in range(10)]
    # Each closure's own value differs from CPython's (the warning), its
    # prefix and length do not: a dangling view would garble both.
    print("head_gen_capture", [f().startswith("gen-key-") for f in fs],
          [len(f()) for f in fs], fs[-1](), len(junk))
    # Over a list the uncaptured head stays a view of the element.
    print("head_list_view", [len(k) for k, n in ps])  # tpyc: ok


def demote(rows: list[list[int32]]) -> None:
    xs = [1, 2, 3]
    r = [xs.pop() for xs in rows]  # tpyc: ok
    print("demote", xs[0], r)


def hold(r: Own[R]) -> Own[R]:
    return r


def own_param(p: Own[R], ps: list[R]) -> int:  # tpyc: warning(/param 'p' is never consumed/)
    # The variable's consume is not the hidden Own parameter's.
    xs = [hold(p) for p in ps]  # tpyc: warning(/copies R into owned storage/)
    return len(xs)


def own_param_twin(p: Own[R], ps: list[R]) -> int:  # tpyc: warning(/param 'p' is never consumed/)
    xs = [hold(q) for q in ps]  # tpyc: warning(/copies R into owned storage/)
    return len(xs)


def own_alias(p: Own[R], ps: list[R]) -> int:  # tpyc: warning(/param 'p' is never consumed/)
    q = p
    # The variable is no alias of the Own parameter: its consume is its own.
    xs = [hold(q) for q in ps]  # tpyc: warning(/copies R into owned storage/)
    return len(xs)


def own_opt_pairs() -> Iterator[Own[tuple[B | None, int]]]:
    yield (B(1), 1)
    yield (None, 2)


def own_rec_pairs() -> Iterator[Own[tuple[B, int]]]:
    yield (B(3), 1)
    yield (B(4), 2)


def own_member_pairs() -> Iterator[tuple[Own[R], int]]:
    yield (R(5), 1)


def for_head_move() -> None:
    # A for head moved out of an owning step binds non-const: a move, which
    # the @nocopy B shows (a copy would not build).
    zs: list[B | None] = []
    for b, m in own_opt_pairs():
        zs.append(b)  # tpyc: ok
    # The record head, and the comprehension twin.
    ys: list[B] = []
    for c, n in own_rec_pairs():
        ys.append(c)  # tpyc: ok
    ws = [c for c, n in own_rec_pairs()]  # tpyc: ok
    # An `Own` member beside a non-register one (BigInt): the step binds
    # mutable and the element moves (the snapshot pins the move).
    rs: list[R] = []
    for d, k in own_member_pairs():
        rs.append(d)  # tpyc: ok
    ys[0].v += 10
    print("for_head_move", len(zs), zs[1] is None, ys[0].v, ws[1].v, rs[0].v)


def consumed(bs: list[B]) -> None:
    b = B(9)
    n = take(b)
    print("consumed", n, [b.get() for b in bs])  # tpyc: ok


def main() -> None:
    capture([5])
    nested_def([5])
    source_capture([5])
    clause1_capture([1, 2], [3])
    demote([[10]])
    bs = [B(1), B(2)]
    consumed(bs)
    rs = [R(1), R(2)]
    print("own_param", own_param(R(0), rs), own_param_twin(R(0), rs),
          own_alias(R(0), rs))
    for_head_move()
    rs[0].v = 7
    print("own_param_after", rs[0].v)
    clause1_lambda([1, 2], [3])
    self_consume()
    same_stmt(bs)
    own_tuple(1)
    lambda_leak([P(1), P(2)])
    inner_take()
    loop_move([R(4)])
    lambda_param_consumed([Bx(1)])
    head_capture({"alpha-long-key-0000000000000000": 1,
                  "beta-long-key-11111111111111111": 2},
                 [("a" * 30, 1), ("b" * 30, 2)], [(b"ab", 1), (b"c", 2)])
    head_gen_capture([("a" * 30, 1), ("bb", 2)])
