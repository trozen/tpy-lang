# A for over a genexpr warns when the loop grows the genexpr's SOURCE; a capture
# may grow unless the yield points into it. Warned mutations never run.
from tpy import int32, Own
from typing import Iterator
import asyncio


def grow(ys: list[int32]) -> None:
    ys.append(0)


def take(ys: Own[list[int32]]) -> Own[list[int32]]:
    return ys


def drain(it: Iterator[int32]) -> int32:
    n = 0
    for _ in it:
        n += 1
    return n


class Rec:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k


class Bag:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]

    def run(self, big: bool) -> int32:
        t = 0
        # method, field source
        for v in (x + 1 for x in self.items):
            if big:
                self.items.append(v)  # tpyc: warning(/Mutation of 'self.items' while iterating/)
            t += v
        return t


def free_fn(xs: list[int32], big: bool) -> int32:
    t = 0
    # free function, direct mutation of the source
    for v in (x * 2 for x in xs):
        if big:
            xs.append(v)  # tpyc: warning(/Mutation of 'xs' while iterating/)
        t += v
    return t


def through_callee(xs: list[int32], big: bool) -> int32:
    t = 0
    # the source handed to a mutating callee inside the loop
    for v in (x * 2 for x in xs):
        if big:
            grow(xs)  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
        t += v
    return t


def in_zip(xs: list[int32], ys: list[int32], big: bool) -> int32:
    t = 0
    # a genexpr nested in a lazy combinator
    for a, b in zip((x for x in xs), ys):
        if big:
            xs.append(a)  # tpyc: warning(/Mutation of 'xs' while iterating/)
        t += a * b
    return t


def unpack(ps: list[tuple[int32, int32]], big: bool) -> int32:
    t = 0
    # a tuple-unpacking genexpr
    for s in (a + b for a, b in ps):
        if big:
            ps.append((s, s))  # tpyc: warning(/Mutation of 'ps' while iterating/)
        t += s
    return t


def dict_view(d: dict[int32, int32], big: bool) -> int32:
    t = 0
    # a dict view source
    for v in (x for x in d.values()):
        if big:
            d.pop(v)  # tpyc: warning(/Mutation of 'd' while iterating/)
        t += v
    return t


def gen_body(xs: list[int32], big: bool) -> Iterator[int32]:
    # generator body
    for v in (x * 2 for x in xs):
        if big:
            xs.append(v)  # tpyc: warning(/Mutation of 'xs' while iterating/)
        yield v


async def async_body(xs: list[int32], big: bool) -> int32:
    t = 0
    # async body
    for v in (x * 2 for x in xs):
        if big:
            xs.append(v)  # tpyc: warning(/Mutation of 'xs' while iterating/)
        t += v
    return t


def view_yield(ys: list[str], xs: list[int32], big: bool) -> str:
    out = ""
    # a str slice yield views an element of the capture `ys`: it keeps the loan
    for s in (ys[i][1:] for i in xs):
        if big:
            ys.append("zzzzzzzz")  # tpyc: warning(/Mutation of 'ys' while iterating/)
        out += s
    return out


def source_is_capture(xs: list[int32], big: bool) -> int32:
    t = 0
    # the source is also read as a capture: the capture's hold must not hide
    # the source's loan
    for v in (x for x in xs if len(xs) > 0):
        if big:
            xs.append(v)  # tpyc: warning(/Mutation of 'xs' while iterating/)
        t += v
    return t


def nested_def(xs: list[int32], big: bool) -> int32:
    # nested def
    def inner() -> int32:
        t = 0
        for v in (x * 3 for x in xs):
            if big:
                xs.append(v)  # tpyc: warning(/Mutation of 'xs' while iterating/)
            t += v
        return t
    return inner()


def capture_moved(xs: list[int32]) -> int32:
    ks = [5, 6]
    n = 0
    # moving a capture the live frame still reads becomes a copy
    for v in (x + len(ks) for x in xs):
        n = len(take(ks))  # tpyc: warning(/copies list\[int32\] into owned storage/)
        break
    return n + len(ks)


def range_capture(n: int32) -> int32:
    seen: set[int32] = set()
    # a two-bound range source takes two leading params before the capture
    for v in (i for i in range(1, n) if i not in seen):
        seen.add(v + 1)  # tpyc: ok
    return len(seen)


def arg_capture(xs: list[int32], seen: set[int32]) -> int32:
    # a genexpr handed to a callee that advances it: the capture is not
    # mutated through it
    return drain(x for x in xs if x not in seen)


def capture_grows(xs: list[int32]) -> int32:
    seen: set[int32] = set()
    n = 0
    # a captured container is held whole: growing it is fine, and each pull
    # sees the grown set, as CPython does
    for v in (x for x in xs if x not in seen):
        seen.add(v)  # tpyc: ok
        n += 1
    return n


def str_capture_grows(ws: list[str]) -> int32:
    seen: set[str] = set()
    n = 0
    # a str yield is an owned copy, so the captured set may still grow
    for w in (x for x in ws if x not in seen):
        seen.add(w)  # tpyc: ok
        n += 1
    return n


def owned_str_yield(ys: list[str], xs: list[int32]) -> str:
    out = ""
    # an indexed str is yielded as an owned copy: the capture may grow
    for s in (ys[i] for i in xs):
        ys.append("!")  # tpyc: ok
        out += s
    return out + str(len(ys))


def record_from_source(rs: list[Rec]) -> int32:
    seen: set[int32] = set()
    # the record yield points into the source, not into the capture; the
    # mutation through it shows the element is lent, not copied
    for r in (q for q in rs if q.k not in seen):
        seen.add(r.k)  # tpyc: ok
        r.k += 100
    return rs[0].k + rs[1].k


def after_loop(xs: list[int32]) -> int32:
    t = 0
    for v in (x * 2 for x in xs):
        t += v
    # the loan ends with the loop
    xs.append(t)  # tpyc: ok
    return len(xs)


def consumer_arg(xs: list[int32]) -> int32:
    # a consumer pulls to the end inside the statement: no loan outlives it
    t = sum(x for x in xs)
    xs.append(t)  # tpyc: ok
    return len(xs)


def range_source(xs: list[int32]) -> int32:
    # a range source iterates no container
    for v in (i for i in range(len(xs))):
        xs.append(v)  # tpyc: ok
    return len(xs)


def main() -> None:
    print("method:", Bag().run(False))
    print("free_fn:", free_fn([1, 2, 3], False))
    print("through_callee:", through_callee([1, 2, 3], False))
    print("in_zip:", in_zip([1, 2], [3, 4], False))
    print("unpack:", unpack([(1, 2), (3, 4)], False))
    print("dict_view:", dict_view({1: 10, 2: 20}, False))
    print("gen_body:", sum(gen_body([1, 2], False)))
    print("async_body:", asyncio.run(async_body([1, 2], False)))
    print("view_yield:", view_yield(["abc", "def"], [1, 0], False))
    print("source_is_capture:", source_is_capture([1, 2], False))
    print("nested_def:", nested_def([1, 2], False))
    print("capture_moved:", capture_moved([1, 2]))
    print("range_capture:", range_capture(5))
    print("arg_capture:", arg_capture([1, 2, 3], {2}))
    print("capture_grows:", capture_grows([1, 2, 1, 3, 2]))
    print("str_capture_grows:", str_capture_grows(["a", "b", "a", "c"]))
    print("owned_str_yield:", owned_str_yield(["ab", "cd"], [1, 0]))
    print("record_from_source:", record_from_source([Rec(1), Rec(2)]))
    print("after_loop:", after_loop([1, 2]))
    print("consumer_arg:", consumer_arg([1, 2]))
    print("range_source:", range_source([5, 6]))


big_module = False
gl = [1, 2, 3]
gt = 0
# module level
for gv in (x * 2 for x in gl):
    if big_module:
        gl.append(gv)  # tpyc: warning(/Mutation of 'gl' while iterating/)
    gt += gv
print("module:", gt)

main()
