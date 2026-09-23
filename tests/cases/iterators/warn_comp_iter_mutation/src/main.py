# A comprehension or `for` growing its own source warns as a `for` over it
# does; warned writes never run.
from tpy import int32, Own
from typing import Iterator
import asyncio


def grow(ys: list[int32]) -> bool:
    ys.append(7)
    return True


def grow_rows(rs: list[list[int32]]) -> bool:
    rs.append([7])
    return True


def grow_set(s: set[int32]) -> bool:
    s.add(len(s) + 100)
    return True


def pick(ys: list[int32]) -> list[int32]:
    return ys


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def bump(self, by: int32) -> int32:
        self.v += by
        return self.v


class Bag:
    items: list[int32]
    n: int32

    def __init__(self, xs: list[int32], big: bool) -> None:
        self.items = [1, 2]
        # constructor, source is a param
        self.n = len([v for v in xs if big and grow(xs)])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)

    def run(self, big: bool) -> int32:
        # method, self field source, direct mutation
        return len([v for v in self.items if big and self.items.pop() > 0])  # tpyc: warning(/Mutation of 'self.items' while iterating/)

def free_fn(xs: list[int32], big: bool) -> int32:
    # free function, the reproducer: a condition hands the source to a
    # mutating callee
    return len([v for v in xs if len(xs) < 5000 and big and grow(xs)])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)


def direct(xs: list[int32], big: bool) -> int32:
    # direct method mutation of the source in the condition
    return len([v for v in xs if big and xs.pop() > 0])  # tpyc: warning(/Mutation of 'xs' while iterating/)


def element(xs: list[int32], big: bool) -> int32:
    # the ELEMENT grows the source
    return len([big and grow(xs) for v in xs])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)


def set_comp(xs: list[int32], big: bool) -> int32:
    # set comprehension
    return len({v for v in xs if big and grow(xs)})  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)


def dict_comp(xs: list[int32], big: bool) -> int32:
    # dict comprehension, the VALUE grows the source
    return len({v: big and grow(xs) for v in xs})  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)


def set_source(s: set[int32], big: bool) -> int32:
    # a set source
    return len([v for v in s if big and grow_set(s)])  # tpyc: warning(/Passing borrowed container 's' to non-readonly parameter/)


def dict_view(d: dict[int32, int32], big: bool) -> int32:
    # a dict view source: the loan is filed off the call's borrow provenance
    return len([v for v in d.values() if big and d.setdefault(v + 10, 0) > 0])  # tpyc: warning(/Mutation of 'd' while iterating/)


def call_source(xs: list[int32], big: bool) -> int32:
    # a borrow-returning call source
    return len([v for v in pick(xs) if big and grow(xs)])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)


def nested_comp(xs: list[int32], big: bool) -> int32:
    # a nested comprehension's condition grows the OUTER source
    return len([[w for w in [1, 2] if big and grow(xs)] for v in xs])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)


def gen_body(xs: list[int32], big: bool) -> Iterator[int32]:
    # generator body
    yield len([v for v in xs if big and grow(xs)])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)


async def async_body(xs: list[int32], big: bool) -> int32:
    # async body
    return len([v for v in xs if big and grow(xs)])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)


def nested_def(xs: list[int32], big: bool) -> int32:
    # nested def, the source is a capture
    def inner() -> int32:
        return len([v for v in xs if big and xs.pop() > 0])  # tpyc: warning(/Mutation of 'xs' while iterating/)
    return inner()


def in_for_body(xs: list[int32], zs: list[int32], big: bool) -> int32:
    t = 0
    for v in xs:
        # a comprehension inside a for body: the loop's own loan outlives it
        t += len([w for w in zs])
        if big:
            xs.append(v)  # tpyc: warning(/Mutation of 'xs' while iterating/)
    return t


def enclosing_element_loan(xss: list[list[int32]], big: bool) -> int32:
    t = 0
    for v in xss[0]:
        # the enclosing loop's loan on `xss[0]` survives the comprehension
        # over `xss`
        t += v + len([len(w) for w in xss if big and xss[0].pop() > 0])  # tpyc: warning(/Mutation of 'xss\[\.\.\.\]' while iterating/)
    return t


def enclosing_sibling(xss: list[list[int32]], big: bool) -> int32:
    t = 0
    for v in xss[0]:
        # a SIBLING element of the enclosing loop's source may shrink
        t += v + len([len(w) for w in xss if big and xss[1].pop() > 0])  # tpyc: ok
    return t


def enclosing_unknown_index(xss: list[list[int32]], i: int32, big: bool) -> int32:
    t = 0
    for v in xss[0]:
        # an index that may be the iterated element
        t += v + len([len(w) for w in xss if big and xss[i].pop() > 0])  # tpyc: warning(/may hit the element being iterated/)
    return t


def for_nest_element(xss: list[list[int32]], big: bool) -> int32:
    t = 0
    for v in xss[0]:
        # a nested `for` over the whole list keeps the outer element loan
        for w in xss:
            if big:
                xss[0].pop()  # tpyc: warning(/Mutation of 'xss\[\.\.\.\]' while iterating/)
            t += v + len(w)
    return t


def for_nest_sibling(xss: list[list[int32]], big: bool) -> int32:
    t = 0
    for v in xss[0]:
        for w in xss:
            # a sibling element may still shrink
            if big:
                xss[1].pop()  # tpyc: ok
            t += v + len(w)
    return t


class Box:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]

    def items_m(self) -> list[int32]:
        return self.items


def mk_box() -> Own[Box]:
    return Box()


def temp_receiver(big: bool) -> int32:
    n = 0
    if big:
        # the source borrows from a temporary receiver that dies before the
        # loop runs (so this never runs)
        n = len([e * 2 for e in mk_box().items_m()])  # tpyc: warning(/Iterator borrows from temporary receiver object/)
    return n


def self_named_source() -> int32:
    ccs = [[[1], [2, 3]], [[4]]]
    t = 0
    for c in ccs:
        # the source `c` is the ENCLOSING binding, which the comprehension
        # cannot reach by name: growing the target `c` grows an element, not
        # the source, so no iteration warning
        t += sum([len(c) for c in c if grow(c)])  # tpyc: ok
    return t * 100 + len(ccs[0][0]) * 10 + len(ccs[1][0])


def other_named_source(big: bool) -> int32:
    ccs = [[[1], [2, 3]], [[4]]]
    t = 0
    for c in ccs:
        # the twin with a distinct target still warns when `c` itself grows
        t += sum([len(x) for x in c if big and grow_rows(c)])  # tpyc: warning(/Passing borrowed container 'c' to non-readonly parameter/)
    return t


def other_container(xs: list[int32], ys: list[int32]) -> int32:
    # growing a DIFFERENT container is fine
    return len([v for v in xs if grow(ys)])  # tpyc: ok


def after_comp(xs: list[int32]) -> int32:
    out = [v for v in xs]
    # the loan ends with the comprehension
    xs.append(len(out))  # tpyc: ok
    grow(xs)  # tpyc: ok
    return len(xs)


def range_source(xs: list[int32]) -> int32:
    # a range source iterates no container
    return len([i for i in range(3) if grow(xs)])  # tpyc: ok


def read_only(xs: list[int32]) -> int32:
    # a comprehension that only reads its source
    return len([v + len(xs) for v in xs if v in xs])  # tpyc: ok


def main() -> None:
    seed = [1, 2]
    print("ctor:", Bag(seed, False).n)
    b = Bag(seed, False)
    print("method:", b.run(False))
    print("free_fn:", free_fn([1, 2, 3], False))
    print("direct:", direct([1, 2, 3], False))
    print("element:", element([1, 2, 3], False))
    print("set_comp:", set_comp([1, 2, 2], False))
    print("dict_comp:", dict_comp([1, 2], False))
    print("set_source:", set_source({1, 2}, False))
    print("dict_view:", dict_view({1: 10, 2: 20}, False))
    print("call_source:", call_source([1, 2], False))
    print("nested_comp:", nested_comp([1, 2], False))
    print("gen_body:", sum(gen_body([1, 2], False)))
    print("async_body:", asyncio.run(async_body([1, 2], False)))
    print("nested_def:", nested_def([1, 2], False))
    print("in_for_body:", in_for_body([1, 2], [3], False))
    print("enclosing_element_loan:", enclosing_element_loan([[1, 2], [3]], False))
    print("enclosing_sibling:", enclosing_sibling([[1, 2], [3]], False))
    print("enclosing_unknown_index:", enclosing_unknown_index([[1, 2], [3]], 1, False))
    print("for_nest_element:", for_nest_element([[1, 2], [3]], False))
    print("for_nest_sibling:", for_nest_sibling([[1, 2], [3]], False))
    print("temp_receiver:", temp_receiver(False))
    print("self_named_source:", self_named_source())
    print("other_named_source:", other_named_source(False))
    print("other_container:", other_container([1, 2], [0]))
    print("after_comp:", after_comp([1, 2]))
    print("range_source:", range_source([1]))
    print("read_only:", read_only([1, 2]))


big_module = False
gl = [1, 2, 3]
# module level
gn = len([v for v in gl if big_module and grow(gl)])  # tpyc: warning(/Passing borrowed container 'gl' to non-readonly parameter/)
print("module:", gn)
gcss = [[Cell(1)], [Cell(2)]]
gt = 0
for gc in gcss:
    # module level: a target named like its source's root warns nothing
    gt += sum([gc.bump(1) for gc in gc])  # tpyc: ok
print("module_self_named:", gt, gcss[0][0].v, gcss[1][0].v)

main()
