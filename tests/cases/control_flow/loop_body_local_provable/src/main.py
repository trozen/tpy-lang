# A name first bound in a loop CLAUSE -- body or `else` -- is assigned after
# the loop only when that clause provably ran and the binding is on every path
# through it. The body clause needs a provable head; the `else` clause runs on
# the fall-through path by definition, so what it binds is assigned after the
# loop unless a `break` path can reach the read without it. One section per
# position the rule has to reach; the subject line is the post-loop read.
import asyncio
from enum import Enum
from typing import Iterator

from tpy import int32, error_return, ReturnException


class NotFound(Exception, ReturnException):
    pass


class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Color(Enum):
    RED = 1
    GREEN = 2


class Guard:
    tag: int32

    def __init__(self, tag: int32) -> None:
        self.tag = tag

    def __enter__(self) -> int32:
        return self.tag

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


# free function -- a literal range head
def free_fn() -> int32:
    for i in range(2):
        v = i + 1
    return v  # tpyc: ok


class Holder:
    total: int32

    # constructor -- range(1, 4)
    def __init__(self) -> None:
        for i in range(1, 4):
            v = i * 2
        self.total = v  # tpyc: ok

    # method -- the loop variable keeps aliasing the element it was bound to
    def bump(self) -> int32:
        cells = [Cell(1), Cell(2)]
        for c in cells:
            last = c
        last.n += 100  # tpyc: ok
        return cells[1].n


class ElseHolder:
    tag: int32

    # else clause, constructor -- the field takes what the clause bound
    def __init__(self, n: int32) -> None:
        for i in range(n):
            pass
        else:
            v = 9
        self.tag = v  # tpyc: ok

    # else clause, method
    def widen(self, n: int32) -> int32:
        for i in range(n):
            pass
        else:
            v = self.tag + 1
        return v  # tpyc: ok


# generator -- the body-bound local survives into the yields
def gen_pos() -> Iterator[int32]:
    for i in range(2):
        v = i + 1
    yield v  # tpyc: ok
    yield v * 10


# async -- same local, on the resumable frame
async def async_pos() -> int32:
    await asyncio.sleep(0)
    for i in range(2):
        v = i + 3
    return v  # tpyc: ok


# nested def -- the closure's own loop
def nested_def_pos() -> int32:
    def inner() -> int32:
        for i in range(2):
            v = i + 5
        return v  # tpyc: ok

    return inner()


# nested def declared INSIDE a loop body and holding a loop of its own --
# analyzing it saves and restores the whole function state, which must leave
# the enclosing loop's own edge collectors balanced
def nested_def_in_body() -> int32:
    for i in range(2):
        def inner() -> int32:
            for j in range(2):
                v = j + 1
            return v  # tpyc: ok

        w = i + inner()
    return w  # tpyc: ok


# if-arm -- one arm runs a provable loop, the other binds directly
def if_arm(flag: bool) -> int32:
    if flag:
        for i in range(2):
            z = i + 1
    else:
        z = 7
    return z  # tpyc: ok


# nested loops -- both heads provable
def nested_loops() -> int32:
    for i in range(2):
        for j in range(2):
            v = i * 10 + j
    return v  # tpyc: ok


# nested loops -- the OUTER head proves nothing, so the inner loop's binding
# needs a value before the outer loop
def nested_outer_unprovable(xs: list[int32]) -> int32:
    v = -1
    for x in xs:
        for j in range(2):
            v = x + j
    return v  # tpyc: ok


# while parity -- the head the value ranges decide
def while_parity() -> int32:
    i = 0
    while i < 3:
        w = i
        i += 1
    return w  # tpyc: ok


# a local whose only binding is a non-empty container literal
def literal_local() -> int32:
    items = [10, 20, 30]
    for x in items:
        v = x
    return v  # tpyc: ok


# range(1, n) with n narrowed by the guard above it
def narrowed_bound(n: int32) -> int32:
    if n < 2:
        return -1
    for i in range(1, n):
        v = i * 2
    return v  # tpyc: ok


# a non-empty literal straight at the head
def literal_head() -> int32:
    for x in [4, 5, 6]:
        v = x
    return v  # tpyc: ok


# three-arg range, positive step -- the step's sign decides which comparison
# proves the head non-empty
def range_step_pos() -> int32:
    for i in range(0, 6, 2):
        v = i
    return v  # tpyc: ok


# three-arg range, negative step -- start > stop is what proves this one
def range_step_neg() -> int32:
    for i in range(3, 0, -1):
        v = i
    return v  # tpyc: ok


# an unprovable head is fine once the name has a value before the loop
def pre_assigned(xs: list[int32]) -> int32:
    v = 0
    for x in xs:
        v = x
    return v  # tpyc: ok


# the loop VARIABLE itself, after a provable loop
def loop_var_after() -> int32:
    for i in range(3):
        pass
    return i  # tpyc: ok


# context-manager body
def with_body() -> int32:
    with Guard(5) as t:
        for i in range(2):
            v = t + i
    return v  # tpyc: ok


# try/finally body
def try_finally() -> int32:
    try:
        for i in range(2):
            v = i + 20
    finally:
        pass
    return v  # tpyc: ok


# match arm
def match_arm(k: int32) -> int32:
    match k:
        case 1:
            for i in range(2):
                v = i + 30
            return v  # tpyc: ok
        case _:
            return -1


# break join, no else -- the binding sits ABOVE the break, so every edge out of
# the body carries it
def break_above_bind(flag: bool) -> int32:
    for i in range(3):
        w = i + 1
        if flag:
            break
    return w  # tpyc: ok


# `while True:` -- the head is never false, so the break edges are the only
# ones that leave the loop and the binding on them is what decides
def while_true_break() -> int32:
    i = 0
    while True:
        i += 1
        if i > 2:
            total = i * 10
            break
    return total  # tpyc: ok


def next_val(i: int32) -> int32:
    return 3 - i


# a walrus in the `while` head counts after the loop exactly where the head
# necessarily evaluated it -- a plain one on every path, a break included
def while_walrus_break() -> int32:
    i = 0
    while (n := next_val(i)) > 0:
        i += 1
        if n == 1:
            break
    return n  # tpyc: ok


# an `and` walrus runs only once the operands before it are truthy, so it is
# assigned inside the BODY (the head came out true to get there)
def while_walrus_and(flag: bool) -> int32:
    total = 0
    i = 0
    while flag and (b := next_val(i)) > 0:
        total += b  # tpyc: ok
        i += 1
    return total


# an `or` walrus runs only once the operands before it are falsy, so it is
# assigned on the edge that LEAVES through the head -- which is the only way
# out of this loop
def while_walrus_or() -> int32:
    i = 0
    while i > 5 or (c := next_val(i)) > 0:
        i += 1
    return c  # tpyc: ok


# the `and` walrus over a subscript source -- the body is the head's TRUE path,
# which by definition evaluated every operand
def while_walrus_and_subscript(s: str) -> int32:
    i = 0
    hits = 0
    while i < len(s) and (c := s[i]) != " ":
        if c == "a":  # tpyc: ok
            hits += 1
        i += 1
    return hits


# the head-exit edge reaches the `else` clause too, so the `or` walrus is
# assigned there -- the `else` runs on exactly the path that left through the
# head
def while_walrus_or_else() -> int32:
    i = 0
    total = 0
    while i > 5 or (c := next_val(i)) > 0:
        i += 1
    else:
        total = c  # tpyc: ok
    return total


# `not (a or b)` is true only when BOTH operands came out falsy, so the body
# may read the target the `or`'s right operand bound
def while_not_or_body() -> int32:
    i = 0
    total = 0
    while not (i > 5 or (d := next_val(i)) < 0):
        total += d  # tpyc: ok
        i += 1
    return total


# nested: the TRUE path of `(a and (b := ..)) or (c := ..)` may have taken
# either side of the `or`, so it guarantees neither target; the FALSE path ran
# both sides and so guarantees the right one
def while_nested_or_after(flag: bool) -> int32:
    i = 0
    while (flag and (b := next_val(i)) > 0) or (c := next_val(i)) > 5:
        i += 1
    return c  # tpyc: ok


# the `if` twins of the two above -- an `if` head and a `while` head ask one
# question, so they answer it the same way
def if_head_walrus(flag: bool) -> int32:
    total = 0
    if flag and (b := next_val(0)) > 0:
        total += b  # tpyc: ok
    if not (flag or (d := next_val(1)) < 0):
        total += d  # tpyc: ok
    return total


# the for/else search idiom -- the break path binds `found`, the else binds it
# on the exhausted path
def search(cells: list[Cell], want: int32) -> int32:
    for c in cells:
        if c.n == want:
            found = c.n
            break
    else:
        found = -1
    return found  # tpyc: ok


# else clause, free function -- an unprovable head proves nothing about the
# body, but the `else` ran on the path that reaches the return
def else_free_fn(n: int32) -> int32:
    for i in range(n):
        pass
    else:
        v = 9
    return v  # tpyc: ok


# else clause, while arm
def else_while(k: int32) -> int32:
    i = 0
    while i < k:
        i += 1
    else:
        v = 9
    return v  # tpyc: ok


# else clause, enum-for arm
def else_enum() -> int32:
    for c in Color:
        pass
    else:
        v = 9
    return v  # tpyc: ok


# else clause, nested def
def else_nested_def(n: int32) -> int32:
    def inner() -> int32:
        for i in range(n):
            pass
        else:
            v = 9
        return v  # tpyc: ok

    return inner()


# else clause, match arm
def else_match_arm(k: int32) -> int32:
    match k:
        case 1:
            for i in range(k):
                pass
            else:
                v = 9
            return v  # tpyc: ok
        case _:
            return -1


# else clause, @error_return body
@error_return(NotFound)
def else_error_return(n: int32) -> int32:
    for i in range(n):
        pass
    else:
        v = 9
    if v < 0:
        raise NotFound
    return v  # tpyc: ok


# else clause, tuple-unpack str target -- the `__tup` the unpack materializes is
# the else block's, so the hoisted slot must OWN the string rather than view it
def pair() -> tuple[str, int32]:
    return ("abcdefghijklmnopqrstuvwxyz0123456789ABCD", 7)


def else_tuple_str(n: int32) -> None:
    for i in range(n):
        pass
    else:
        s, k = pair()
    print("else_tuple_str", s, k)  # tpyc: ok


# else clause, reference type -- mutating the else binding after the loop must
# be visible through the container it came from, so a silent copy would fail
def else_ref() -> int32:
    cells = [Cell(1), Cell(2)]
    for c in cells:
        pass
    else:
        last = cells[1]
    last.n += 100  # tpyc: ok
    return cells[1].n


# body and else bind one name -- one declaration, either clause assigns it
def else_join(n: int32) -> int32:
    total = 0
    for i in range(n):
        v = i + 1
        total += v
    else:
        v = 9
    return total + v  # tpyc: ok


# an else binding read inside a LATER loop's else clause
def else_read_in_else(n: int32) -> int32:
    for i in range(n):
        pass
    else:
        v = 9
    for j in range(n):
        pass
    else:
        w = v + 1  # tpyc: ok
    return w


# a break in a NESTED inner loop leaves the outer loop's else clause alone
def else_inner_break() -> int32:
    for i in range(2):
        for j in range(3):
            break
    else:
        v = 9
    return v  # tpyc: ok


# generator -- the break path binds the name too, so it joins with the else
def else_gen(flag: bool) -> Iterator[int32]:
    for i in range(2):
        v = i + 1
        if flag:
            break
    else:
        v = 9
    yield v  # tpyc: ok


# async -- the same join on a resumable frame
async def else_async(flag: bool) -> int32:
    await asyncio.sleep(0)
    for i in range(2):
        v = i + 1
        if flag:
            break
    else:
        v = 9
    return v  # tpyc: ok


def main() -> None:
    print("free_fn", free_fn())
    h = Holder()
    print("constructor", h.total)
    # 102 proves the post-loop binding aliased the element rather than copying it
    print("method", h.bump())
    g = gen_pos()
    for y in g:
        print("generator", y)
    print("async", asyncio.run(async_pos()))
    print("nested_def", nested_def_pos())
    print("nested_def_in_body", nested_def_in_body())
    print("if_arm", if_arm(True), if_arm(False))
    print("nested_loops", nested_loops())
    print("nested_outer_unprovable", nested_outer_unprovable([]))
    print("while_parity", while_parity())
    print("literal_local", literal_local())
    print("narrowed_bound", narrowed_bound(4))
    print("literal_head", literal_head())
    print("range_step_pos", range_step_pos())
    print("range_step_neg", range_step_neg())
    print("pre_assigned", pre_assigned([]))
    print("loop_var_after", loop_var_after())
    print("with_body", with_body())
    print("try_finally", try_finally())
    print("match_arm", match_arm(1))
    print("break_above_bind", break_above_bind(True), break_above_bind(False))
    print("while_true_break", while_true_break())
    print("while_walrus_break", while_walrus_break())
    print("while_walrus_and", while_walrus_and(True), while_walrus_and(False))
    print("while_walrus_or", while_walrus_or())
    print("while_walrus_and_subscript", while_walrus_and_subscript("aba xa"))
    print("while_walrus_or_else", while_walrus_or_else())
    print("while_not_or_body", while_not_or_body())
    print("while_nested_or_after", while_nested_or_after(True),
          while_nested_or_after(False))
    print("if_head_walrus", if_head_walrus(True), if_head_walrus(False))
    cells = [Cell(3), Cell(4)]
    print("search", search(cells, 4), search(cells, 99))
    eh = ElseHolder(0)
    print("else_constructor", eh.tag)
    print("else_method", eh.widen(0))
    print("else_match_arm", else_match_arm(1))
    try:
        er = else_error_return(0)
    except NotFound:
        print("else_error_return", "not found")
    else:
        print("else_error_return", er)
    else_tuple_str(0)
    print("else_free_fn", else_free_fn(0), else_free_fn(2))
    print("else_while", else_while(0), else_while(2))
    print("else_enum", else_enum())
    print("else_nested_def", else_nested_def(0))
    # 102 proves the else binding aliased the element rather than copying it
    print("else_ref", else_ref())
    print("else_join", else_join(0), else_join(2))
    print("else_read_in_else", else_read_in_else(0))
    print("else_inner_break", else_inner_break())
    for y in else_gen(False):
        print("else_gen", y)
    for y in else_gen(True):
        print("else_gen_broke", y)
    print("else_async", asyncio.run(else_async(False)),
          asyncio.run(else_async(True)))


main()

# module level -- a top-level loop's body binding, read after the loop
for i in range(2):
    m = i + 40
print("module_level", m)  # tpyc: ok

# module level -- the same for an else binding
for mi in range(2):
    pass
else:
    mv = 41
print("module_level_else", mv)  # tpyc: ok
