# A value-typed walrus (and `@error_return` / statement-unpack) local binds as
# its declaration twin: rebinds assign in place, a last read copies.
import asyncio
from typing import Any, Iterator
from tpy import int32, Own, ValueType, error_return, ReturnException


# A value walrus rebound by every test of a `while` condition takes the
# binding record of its declaration: read after the loop, its last use
# copies, as the declaration's does.
def while_rebind_int(xs: list[int32]) -> int32:
    i = 0
    total = 0
    while (c := xs[i] + 1) < 3:
        i += 1
    total = c  # the walrus target's last use: copied, as a declaration's
    return total


def while_rebind_str(xs: list[str]) -> str:
    i = 0
    kept = ""
    while len(w := xs[i] + "!") < 3:
        i += 1
    # The walrus target's last use: copied, as a declaration's
    # (BUGS.md#owned-viewfam-local-last-use-copies).
    kept = w
    return kept


# free function: a str walrus local read by name under a live iterator of it.
def iter_live(xs: list[str]) -> str:
    kept = ""
    if len(x := xs[0] + "!") > 0:
        it = iter(x)
        kept = x  # tpyc: ok
        out = ""
        for c in it:
            out += str(c)
        print("iter_live", out)
    return kept


# free function: the same under a live `reversed` iterator.
def reversed_live(xs: list[str]) -> str:
    kept = ""
    if len(x := xs[0] + "!") > 0:
        it = reversed(x)
        kept = x  # tpyc: ok
        out = ""
        for c in it:
            out += str(c)
        print("reversed_live", out)
    return kept


def pick(xs: list[str]) -> str | None:
    if len(xs) == 0:
        return None
    return xs[0] + "?"


# free function: a `str | None` walrus local under a live iterator.
def opt_iter_live(xs: list[str]) -> str:
    kept = ""
    if (w := pick(xs)) is not None:
        it = iter(w)
        kept = w  # tpyc: ok
        out = ""
        for c in it:
            out += str(c)
        print("opt_iter_live", out)
    return kept


# method body: the `str | None` walrus local under a live iterator.
class Picker:
    calls: int32

    def __init__(self) -> None:
        self.calls = 0

    def first_live(self, xs: list[str]) -> str:
        self.calls += 1
        kept = ""
        if (w := pick(xs)) is not None:
            it = iter(w)
            kept = w  # tpyc: ok
            out = ""
            for c in it:
                out += str(c)
            print("method_iter_live", out)
        return kept


def opt_i(k: int32) -> int32 | None:
    if k < 0:
        return None
    return k


# generator: an `int32 | None` walrus frame field read after a `yield`.
def gen_opt(k: int32) -> Iterator[int32]:
    if (x := opt_i(k)) is not None:
        yield 0
        yield x + 1  # tpyc: ok


def opt_big(k: int) -> int | None:
    if k < 0:
        return None
    return k


# generator: an `int | None` walrus frame field read after a `yield`.
def gen_opt_big(k: int) -> Iterator[int]:
    if (x := opt_big(k)) is not None:
        yield 0
        yield x * 10  # tpyc: ok


# generator: an `int32 | None` walrus bound in a plain statement, read after
# a `yield`.
def gen_stmt_opt(k: int32) -> Iterator[int32]:
    y = (x := opt_i(k))
    yield 0
    if x is not None:
        yield x + 1  # tpyc: ok
    if y is not None:
        yield y


# async: an `int32 | None` walrus frame field read after an `await`.
async def async_opt(k: int32) -> int32:
    if (x := opt_i(k)) is not None:
        await asyncio.sleep(0)
        return x + 1  # tpyc: ok
    return -1


# Owning sinks still move the walrus value out.
def take_own(s: Own[str], out: list[str]) -> None:
    out.append(s)


# free function: a str walrus local at an `Own[str]` argument.
def own_arg(xs: list[str], out: list[str]) -> None:
    if len(w := xs[0] + "!") > 0:
        take_own(w, out)  # tpyc: ok


# free function: a str walrus local appended to a list.
def own_append(xs: list[str], out: list[str]) -> None:
    if len(w := xs[0] + "#") > 0:
        out.append(w)  # tpyc: ok


class Fail(Exception, ReturnException):
    pass


@error_return(Fail)
def make_big(n: int) -> int:
    return n + 1


# @error_return body: the unwrapped call result copies at an owning sink, as
# its declaration twin (BUGS.md#owned-value-local-last-use-copies).
@error_return(Fail)
def big_append(n: int, out: list[int]) -> None:
    big = make_big(n)
    out.append(big)  # tpyc: ok


@error_return(Fail)
def make_s(x: str) -> str:
    return x + "!"


# @error_return body: an unwrapped `str` local under a live iterator of it.
@error_return(Fail)
def er_iter(x: str) -> None:
    kept = ""
    s = make_s(x)
    it = iter(s)
    kept = s  # tpyc: ok
    for c in it:
        print("er_iter", c)
    print("er_iter kept", kept)


class V(ValueType):
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


def make_v(n: int) -> Own[V] | None:
    if n < 0:
        return None
    return V(n)


# free function: an `Own[V] | None` call result, V a ValueType, copies when
# appended, as its declaration twin (BUGS.md#owned-value-local-last-use-copies).
def opt_value_append(n: int, out: list[V | None]) -> None:
    r = make_v(n)
    out.append(r)  # tpyc: ok


class R:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


@error_return(Fail)
def make_r(n: int) -> Own[R]:
    return R(n)


# @error_return body: a reference-typed unwrapped result still moves into the
# list (no value type, no copy); the element is the object the local held.
@error_return(Fail)
def ref_movers(n: int, out: list[R]) -> None:
    r = make_r(n)
    out.append(r)  # tpyc: ok
    out[0].n += 10
    print("ref_movers", out[0].n, len(out))


# Any sinks: the walrus str converts (a copy) as its declaration twin does
# (BUGS.md#owned-viewfam-local-last-use-copies).
def take_any(v: Any) -> None:
    print("any_arg", v)


def any_sinks(xs: list[str]) -> Any:
    if len(w := xs[0] + "!") > 0:
        take_any(w)  # tpyc: ok
    out: list[Any] = []
    if len(u := xs[0] + "?") > 0:
        out.append(u)  # tpyc: ok
    print("any_append", out[0])
    if len(t := xs[0] + "#") > 0:
        return t  # tpyc: ok
    return 0


# statement unpack: an `Own[V]` element binds as its declaration twin and
# copies at the append (BUGS.md#owned-value-local-last-use-copies).
def unpack_value(p: tuple[Own[V], int32], out: list[V]) -> None:
    v, k = p
    out.append(v)  # tpyc: ok
    print("unpack_value", k)


def main() -> None:
    n = 5
    while (n := n - 1) > 0:
        print(n)

    x: int32 | None = 5
    if (x := None) is None:
        print("none")

    s = "asd"
    if (s := s + "!"):  # owned source: must promote s to owned, else it dangles
        print(s)
    print(s)

    b = b"hi"
    if (b := b + b"!"):  # bytes view, same owned-source promotion as str
        print(len(b))

    # Comprehension walrus rebinds the enclosing-scope local; the rebind must
    # still assign in place across the comprehension's scope handling.
    m: int32 = 10
    xs = [m for _ in range(3) if (m := m - 1) > 0]
    print(m, len(xs))

    print("while_rebind", while_rebind_int([1, 5]),
          while_rebind_str(["a", "bb"]))

    # Each call prints, so it runs before the line that prints its result
    # (BUGS.md#print-partial-output-on-raise).
    kept = iter_live(["ab"])
    print("iter_live kept", kept)
    kept = reversed_live(["ab"])
    print("reversed_live kept", kept)
    kept = opt_iter_live(["ab"])
    print("opt_iter_live kept", kept)
    kept = Picker().first_live(["cd"])
    print("method_iter_live kept", kept)
    for v in gen_opt(4):
        print("gen_opt", v)
    for v in gen_stmt_opt(4):
        print("gen_stmt_opt", v)
    for v in gen_stmt_opt(-1):
        print("gen_stmt_opt none", v)
    for gb in gen_opt_big(5):
        print("gen_opt_big", gb)
    print("async_opt", asyncio.run(async_opt(6)))

    owned: list[str] = []
    own_arg(["e"], owned)
    own_append(["f"], owned)
    owned.append("g")  # the list is the caller's: its appends show here
    print("own_sinks", len(owned), owned[0], owned[1], owned[2])
    bigs: list[int] = []
    try:
        big_append(7, bigs)
    except Fail:
        print("big_append failed")
    print("big_append", len(bigs), bigs[0])
    try:
        er_iter("ab")
    except Fail:
        print("er_iter failed")
    opts: list[V | None] = []
    opt_value_append(3, opts)
    refs: list[R] = []
    try:
        ref_movers(1, refs)
    except Fail:
        print("ref_movers failed")
    print("opt_value_append", len(opts))
    take_any(any_sinks(["h"]))
    vs: list[V] = []
    unpack_value((V(9), 2), vs)
    print("unpack_value", len(vs), vs[0].n)

main()
