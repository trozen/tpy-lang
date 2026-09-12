# An rvalue at a generic `T` parameter hoists the `__tmp_N` temp only where the
# INSTANTIATED slot cannot bind it -- one section per position, twins beside.
import asyncio
from typing import Iterator

from tpy import float32, int32, Own, ReturnException, copy, error_return, nocopy


@nocopy
class Cell:
    def __init__(self, n: int32) -> None:
        self.n = n


class Boxed[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = copy(v)

    def holds(self, other: T) -> bool:  # tpyc: ok
        return True


def anyslot[T](name: str, v: T) -> bool:
    return name != ""


def anyslot_i32(name: str, v: int32) -> bool:
    # The monomorphic twin: its slot is `int32_t` by value, and it has always
    # taken the rvalue inline.
    return name != ""


class Missing(Exception, ReturnException):
    pass


def pass_through[T](v: T) -> T:
    return v


def mk_cell() -> Own[Cell]:
    return Cell(7)


def repeat[T](value: T, count: int32) -> Iterator[T]:
    # A SIMPLE generator: the peephole's lambda captures the slot by reference,
    # so its argument keeps the temp at every instantiation.
    i = 0
    while i < count:
        yield value
        i += 1


async def echo[T](value: T) -> T:
    return value


# module-level statement: the same inline rvalue at file scope.
top_flag = anyslot("module", 5)  # tpyc: ok


def free_positions() -> None:
    # The four value-typed instantiations: each renders its rvalue inline.
    a = anyslot("free", 42)  # tpyc: ok
    b = anyslot("free", float32(2.25))  # tpyc: ok
    c = anyslot("free", "lit")  # tpyc: ok
    d = anyslot("free", None)  # tpyc: ok
    print("free", a, b, c, d)


def view_source(k: str) -> None:
    # A `str` PARAM reads as a view, and the generic slot resolves to that same
    # view (`param_val_or_ref_t<std::string>`), so it binds bare -- no temp,
    # exactly as the monomorphic twin's slot does.
    print("view", anyslot("view", k))  # tpyc: ok


def ref_rvalue() -> None:
    # A reference-typed instantiation: the slot is `T&`, which binds no rvalue,
    # so the temp stays. `Cell` is @nocopy, so a copy at that boundary would be
    # a compile error instead of a silent one -- the rvalue source has no second
    # handle to observe a mutation through, which is why this leg takes the
    # other half of the reference-type rule (the alias is observed in
    # `ref_lvalue`, whose source is a name).
    print("ref_rvalue", anyslot("ref", mk_cell()))  # tpyc: ok


def ref_lvalue() -> None:
    # The reference semantics the slot carries: what comes back out of the
    # generic ALIASES the caller's object, so the later mutation is visible on
    # both. A copy would print `1 2` -- and, `Cell` being @nocopy, not compile.
    c = Cell(1)
    seen = pass_through(c)  # tpyc: ok
    seen.n += 1
    print("ref_lvalue", c.n, seen.n)


def method_and_ctor() -> None:
    # Record ctor and record method, both at a value-typed T.
    b = Boxed[int32](3)  # tpyc: ok
    print("method_ctor", b.v, b.holds(4))  # tpyc: ok


def comprehension() -> None:
    xs = [i for i in range(3) if anyslot("comp", i)]  # tpyc: ok
    print("comprehension", len(xs))


def closure() -> None:
    def inner() -> bool:
        return anyslot("closure", 11)  # tpyc: ok
    print("closure", inner())


def cond_operand(flag: bool) -> bool:
    # The conditional operand: with no temp there is no `std::optional` slot
    # and no deferred emplace either.
    return flag or anyslot("cond", 42)  # tpyc: ok


def while_condition() -> int32:
    # A COMPOUND while condition is not a flush position; the inline render
    # needs none.
    n = 0
    while anyslot("while", n) and n < 3:  # tpyc: ok
        n += 1
    return n


def match_arm(tag: int32) -> bool:
    match tag:
        case 1:
            return anyslot("match", 1)  # tpyc: ok
        case _:
            return False


def try_finally() -> None:
    seen = False
    try:
        seen = anyslot("try", 8)  # tpyc: ok
    finally:
        print("try_finally", seen)


class Guard:
    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> str:
        return self.name

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("with_body_exit")


def with_body() -> None:
    with Guard("g") as label:
        print("with_body", label, anyslot("with", 9))  # tpyc: ok


def gen_body() -> Iterator[int32]:
    # GENERATOR body (resumable -- the yield is not a direct loop child), where
    # the call renders inline inside the frame's switch.
    n = 0
    while n < 2:
        if anyslot("genbody", n) and anyslot_i32("genbody", n):  # tpyc: ok
            yield n
        n += 1


@error_return(Missing)
def er_body(n: int32) -> int32:
    # @error_return body: the same inline render under the expected-return tier.
    if not (anyslot("erbody", n) and anyslot_i32("erbody", n)):  # tpyc: ok
        raise Missing
    return n + 1


def error_return_body() -> None:
    try:
        got = er_body(6)
    except Missing:
        print("error_return_body", "missing")
    else:
        print("error_return_body", got)


def generator_body() -> None:
    out = 0
    for v in gen_body():
        out += v
    print("generator_body", out)


def generator_factory() -> None:
    # The factory's frame borrows its slot past the statement, so the argument
    # keeps its temp even at a value-typed instantiation.
    out = 0
    for v in repeat(42, 2):  # tpyc: ok
        out += v
    print("generator", out)


async def async_main() -> int32:
    # The coroutine factory takes the same row as the generator's.
    return await echo(21)  # tpyc: ok


def main() -> None:
    print("module", top_flag)
    free_positions()
    view_source("k")
    ref_rvalue()
    ref_lvalue()
    method_and_ctor()
    comprehension()
    closure()
    print("cond_operand", cond_operand(False))
    print("while_condition", while_condition())
    print("match_arm", match_arm(1))
    try_finally()
    with_body()
    generator_body()
    error_return_body()
    generator_factory()
    print("async", asyncio.run(async_main()))


main()
