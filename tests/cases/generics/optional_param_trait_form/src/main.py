# A generic `T | None` parameter takes its form per instantiation: the value form
# at a value T (the argument passes by value, as in the twin) and the pointer
# form at a reference T (a mutation through it is still the caller's object).
# Inverses elsewhere: the ValueType-bounded T that keeps `std::optional<T>`
# spelled (generics/generic_optional_value_bound), and `match` over a generic
# `T | None` subject, a pre-existing reject (BUGS.md) with no section here.
import asyncio
from typing import Iterator

from tpy import Int32, ReturnException, error_return, nocopy


class Stop(Exception, ReturnException):
    pass


class Cell:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


@nocopy
class Pinned:
    # The async reference-T section's payload. A coroutine that captured it by
    # VALUE would need the copy constructor `@nocopy` deletes, so the frame
    # holding a borrow is a build-time fact, not an inference from output --
    # which matters because a generic body cannot read an open `T` back out
    # (every spelling of that rejects today, so `is None` is all it can print).
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Guard:
    def __enter__(self) -> "Guard":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        pass


def probe_gen[T](val: T | None) -> bool:
    # free function, the subject slot: the None test reads either form.
    return val is not None  # tpyc: ok


def probe_twin(val: Int32 | None) -> bool:
    return val is not None


def first_or[T](val: T | None, fallback: T) -> T:
    # A reference T arrives as a borrow, so the returned payload aliases the
    # caller's object rather than a copy of it.
    if val is None:
        return fallback
    return val  # tpyc: ok


def first_or_twin(val: Cell | None, fallback: Cell) -> Cell:
    if val is None:
        return fallback
    return val


def mk() -> Int32:
    return 5


class Container[T]:
    _val: T | None

    def __init__(self, val: T | None) -> None:
        # ctor position: the slot lifts into the optional field. The lift is a
        # copy at a reference T -- warned, and identically for the twin below.
        self._val = val  # tpyc: warning(/may copy T \| None into field/)

    def probe(self, val: T | None) -> bool:
        # method position: the method mutates nothing, so the const spelling.
        return val is not None  # tpyc: ok

    def held(self) -> bool:
        return self._val is not None


class ContainerTwin:
    _val: Int32 | None

    def __init__(self, val: Int32 | None) -> None:
        self._val = val

    def probe(self, val: Int32 | None) -> bool:
        return val is not None

    def held(self) -> bool:
        return self._val is not None


class CellBox:
    _val: Cell | None

    def __init__(self, val: Cell | None) -> None:
        self._val = val  # tpyc: warning(/copies Cell \| None into field/)

    def held(self) -> bool:
        return self._val is not None


def gen_body(n: Int32) -> Iterator[bool]:
    yield probe_gen(n + 1)  # tpyc: ok
    yield probe_twin(n + 1)


@error_return(Stop)
def er_gen(n: Int32) -> bool:
    return probe_gen(n + 1)  # tpyc: ok


@error_return(Stop)
def er_twin(n: Int32) -> bool:
    return probe_twin(n + 1)


async def probe_async[T](val: T | None) -> bool:
    # The coroutine twin: the frame field is the same per-instantiation slot.
    return val is not None  # tpyc: ok


async def probe_async_twin(val: Int32 | None) -> bool:
    return val is not None


async def probe_pinned_twin(val: Pinned | None) -> bool:
    return val is not None


async def amain(n: Int32) -> None:
    v: Int32 | None = n + 1
    v_none: Int32 | None = None
    c = Cell(5)
    none_cell: Cell | None = None
    print("async_value", await probe_async(v), await probe_async(v_none),
          await probe_async_twin(n + 1), await probe_async_twin(None))
    # reference T: the frame slot is the pointer form at this instantiation, so
    # both coroutines below hold a BORROW of `pin` from creation to the await.
    # The mutation in that window lands on the object they point at, and the
    # payload is `@nocopy`, so a frame that captured a copy would not build.
    pin = Pinned(1)
    co = probe_async(pin)
    co_twin = probe_pinned_twin(pin)
    pin.n = 7
    print("async_ref", await probe_async(c), await probe_async(none_cell),
          await co, await co_twin, pin.n)
    # T = None -- the degenerate instantiation, whose await-arg slot is the
    # unit optional; and an rvalue argument, which must NOT hoist a frame
    # local at a value T (the slot owns there, so there is nothing to borrow).
    print("async_unit", await probe_async(None), await probe_async(mk()))


def main() -> None:
    n = 3
    # value T: the rvalue argument passes straight through, as in the twin.
    print("free_value", probe_gen(n + 1), probe_twin(n + 1))

    # reference T: mutating through the returned borrow is visible on `c`.
    c = Cell(1)
    d = Cell(9)
    got = first_or(c, d)
    got.n = 7
    got_twin = first_or_twin(d, c)
    got_twin.n = 8
    none_cell: Cell | None = None
    print("free_ref", c.n, d.n, first_or(none_cell, d).n, probe_gen(none_cell))
    print("free_ref_twin", first_or_twin(none_cell, d).n)

    box = Container[Int32](None)
    held = Container[Int32](n + 1)
    twin = ContainerTwin(n + 1)
    print("method", box.probe(n + 1), twin.probe(n + 1))
    print("method_none", box.probe(None), twin.probe(None))
    print("ctor", box.held(), held.held(), twin.held())

    # a reference-typed field store: warned on both lines above, so the case
    # verifies the generic and the twin diagnose the copy alike.
    print("field_ref", Container[Cell](c).held(), CellBox(c).held())

    # comprehension position
    flags = [probe_gen(i - 1) for i in range(2)]
    flags_twin = [probe_twin(i - 1) for i in range(2)]
    print("comprehension", flags[0], flags_twin[0])

    # conditional-operand position: the argument renders inside the branch.
    ready = False
    print("cond_operand", ready or probe_gen(n + 1), ready or probe_twin(n + 1))

    # generator body
    gen_flags = [f for f in gen_body(n)]
    print("generator", gen_flags[0], gen_flags[1])

    # closure body
    def closure() -> bool:
        return probe_gen(n + 1)  # tpyc: ok

    def closure_twin() -> bool:
        return probe_twin(n + 1)

    print("closure", closure(), closure_twin())

    # context-manager body
    with Guard():
        print("with", probe_gen(n + 1), probe_twin(n + 1))

    # try / finally bodies
    try:
        print("try", probe_gen(n + 1), probe_twin(n + 1))
    finally:
        print("finally", probe_gen(n + 1), probe_twin(n + 1))

    # @error_return body
    try:
        print("error_return", er_gen(n), er_twin(n))
    except Stop:
        print("error_return raised")

    asyncio.run(amain(n))


main()

# module-level statement position. The Int32() spelling is load-bearing: a bare
# int literal at a generic `T | None` slot is not admitted (see BUGS.md).
print("module", probe_gen(Int32(2)), probe_twin(Int32(2)))
