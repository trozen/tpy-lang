# REBINDING a pointer-repr `Optional[container]` local to a container LITERAL.
# The literal is an owned rvalue exactly like the call result the reseat arm
# already took, and it fills the same decl-site rebind slot, so it renders
# `xs = &*(__slot_N = std::vector<int32_t>{1, 2, 3});` -- or, where sema's
# alias-rebind pass proves nothing holds the superseded container, the in-place
# `(*xs) = ...`. Its element types resolve against the SLOT's inner, the way
# the first-binding decl arm resolves them. Each section mutates the container
# after the rebind and prints the result, so a slot that did not take the new
# value would show up as a wrong length.
# In a resumable body the slot is a PRESCANNED frame field instead (an inline
# slot would die at the next suspension), so the same rebind renders
# `xs = &*(__ptr_slot_fN = ...)`.
import asyncio
from typing import Iterator

from tpy import int32, Own


class F:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def make(self) -> Own[list[int32]]:
        return [self.n, self.n]


# free function: a None-initialized local (no slot filled at the decl) rebound
# to a literal on one path only, so the pointer must survive the other path
def none_then_literal(c: bool) -> int32:
    xs: list[int32] | None = None
    if c:
        xs = [1, 2, 3]  # tpyc: ok
    if xs is None:
        return 0
    xs.append(4)
    return len(xs)


# a call result first, then the literal
def call_then_literal(f: F) -> int32:
    a: list[int32] | None = f.make()
    a = [1, 2, 3]  # tpyc: ok
    if a is None:
        return 0
    a.append(4)
    return len(a)


# literal first, literal again
def literal_then_literal(c: bool) -> int32:
    xs: list[int32] | None = [9]
    if c:
        xs = [1, 2, 3]  # tpyc: ok
    if xs is None:
        return 0
    xs.append(4)
    return len(xs)


# a None arm between the two literals
def literal_none_literal(c: bool) -> int32:
    xs: list[int32] | None = [9]
    if c:
        xs = None
    xs = [1, 2, 3, 4]  # tpyc: ok
    if xs is None:
        return 0
    xs.append(5)
    return len(xs)


# method position, dict literal
class Holder:
    f: F

    def __init__(self, n: int32) -> None:
        self.f = F(n)

    def build(self, c: bool) -> int32:
        d: dict[int32, int32] | None = None
        if c:
            d = {1: 10, 2: 20}  # tpyc: ok
        if d is None:
            return 0
        d[3] = 30
        return len(d)


# branch-hoisted local: first bound inside a branch, rebound to a literal
# later, so the reseat takes its own lazily-allocated function-top slot
def branch_hoisted(f: F, c: bool, d: bool) -> int32:
    if c:
        xs: list[int32] | None = f.make()
    else:
        xs = None
    if d:
        xs = [1, 2, 3]  # tpyc: ok
    if xs is None:
        return 0
    xs.append(4)
    return len(xs)


# generator: the None-first flavor across a yield, where the literal fills the
# prescanned frame field and the pointer field re-points
def gen_literal(c: bool) -> Iterator[int32]:
    xs: list[int32] | None = None
    yield 0 if xs is None else len(xs)
    if c:
        xs = [1, 2, 3]  # tpyc: ok
    if xs is not None:
        xs.append(4)
    yield 0 if xs is None else len(xs)


# generator: a call result first, so the pointer is provably non-null and the
# literal is written THROUGH it -- no frame field of its own
def gen_call_then_literal(f: F) -> Iterator[int32]:
    a: list[int32] | None = f.make()
    yield 0 if a is None else len(a)
    a = [1, 2, 3]  # tpyc: ok
    if a is not None:
        a.append(4)
    yield 0 if a is None else len(a)


# async: the coroutine sibling, dict literal
async def coro_literal(c: bool) -> int32:
    d: dict[int32, int32] | None = None
    await asyncio.sleep(0)
    if c:
        d = {1: 10, 2: 20}  # tpyc: ok
    if d is None:
        return 0
    d[3] = 30
    return len(d)


def main() -> None:
    print("none then literal:", none_then_literal(True),
          none_then_literal(False))
    print("call then literal:", call_then_literal(F(3)))
    print("literal then literal:", literal_then_literal(True),
          literal_then_literal(False))
    print("literal none literal:", literal_none_literal(True),
          literal_none_literal(False))
    print("dict:", Holder(3).build(True), Holder(3).build(False))
    print("branch hoisted:", branch_hoisted(F(3), True, True),
          branch_hoisted(F(3), True, False), branch_hoisted(F(3), False, True),
          branch_hoisted(F(3), False, False))
    for n in gen_literal(True):
        print("gen literal:", n)
    for n in gen_literal(False):
        print("gen literal off:", n)
    for n in gen_call_then_literal(F(3)):
        print("gen call then literal:", n)
    print("coro literal:", asyncio.run(coro_literal(True)),
          asyncio.run(coro_literal(False)))


main()
