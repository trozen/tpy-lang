# One comprehension growing its source per position: constructor, method,
# free function, generator, async body, nested def, and per container kind.
from tpy import int32
from typing import Iterator
import asyncio
from fixtures import grow, grow_set, grow_strs, pick, pick_strs


class Bag:
    items: list[int32]
    n: int32

    def __init__(self, xs: list[int32], big: bool) -> None:  # tpyc: mir(conflict /replacement/)
        self.items = [1, 2]
        # constructor, source is a param
        self.n = len([v for v in xs if big and grow(xs)])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/) warning(/may move the elements of 'xs'. but the iteration over 'xs' still walks them/)

    def run(self, big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
        # method, self field source, direct mutation
        return len([v for v in self.items if big and self.items.pop() > 0])  # tpyc: warning(/Mutation of 'self.items' while iterating/) warning(/may move the elements of 'self.items'. but the iteration over 'self.items' still walks them/)


def direct(xs: list[int32], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    # direct method mutation of the source in the condition
    return len([v for v in xs if big and xs.pop() > 0])  # tpyc: warning(/Mutation of 'xs' while iterating/) warning(/may move the elements of 'xs'. but the iteration over 'xs' still walks them/)


def element(xs: list[int32], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    # the ELEMENT grows the source
    return len([big and grow(xs) for v in xs])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/) warning(/may move the elements of 'xs'. but the iteration over 'xs' still walks them/)


def set_comp(xs: list[int32], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    # set comprehension
    return len({v for v in xs if big and grow(xs)})  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/) warning(/may move the elements of 'xs'. but the iteration over 'xs' still walks them/)


def dict_comp(xs: list[int32], big: bool) -> int32:  # tpyc: mir(uncovered /order-sensitive eager operands/)
    # dict comprehension, the VALUE grows the source
    return len({v: big and grow(xs) for v in xs})  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)


def dict_int_key(xs: list[int32], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    # dict comprehension, int key: the filter grows the source (sema is silent)
    return len({v: v for v in xs if big and grow(pick(xs))})  # tpyc: warning(/may move the elements of 'pick\(xs\)'. but the iteration over 'xs' still walks them/)


def dict_int_key_for(xs: list[int32], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    d: dict[int32, int32] = {}
    for v in xs:
        if big and grow(pick(xs)):  # tpyc: warning(/may move the elements of 'pick\(xs\)'. but the iteration over 'xs' still walks them/)
            d[v] = v
    return len(d)


def dict_str_key(ks: list[str], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    # dict comprehension, str key: the filter grows the source (sema is silent)
    return len({k: 1 for k in ks if big and grow_strs(pick_strs(ks))})  # tpyc: warning(/may move the elements of 'pick_strs\(ks\)'. but the iteration over 'ks' still walks them/)


def dict_str_key_for(ks: list[str], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    d: dict[str, int32] = {}
    for k in ks:
        if big and grow_strs(pick_strs(ks)):  # tpyc: warning(/may move the elements of 'pick_strs\(ks\)'. but the iteration over 'ks' still walks them/)
            d[k] = 1
    return len(d)


def set_source(s: set[int32], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    # a set source
    return len([v for v in s if big and grow_set(s)])  # tpyc: warning(/Passing borrowed container 's' to non-readonly parameter/) warning(/may move the elements of 's'. but the iteration over 's' still walks them/)


def dict_view(d: dict[int32, int32], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    # a dict view source: the loan is filed off the call's borrow provenance
    return len([v for v in d.values() if big and d.setdefault(v + 10, 0) > 0])  # tpyc: warning(/Mutation of 'd' while iterating/) warning(/may move the elements of 'd'. but the iteration over 'd.values\(\)' still walks them/)


def call_source(xs: list[int32], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    # a borrow-returning call source
    return len([v for v in pick(xs) if big and grow(xs)])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/) warning(/may move the elements of 'xs'. but the iteration over 'pick\(xs\)' still walks them/)


def nested_comp(xs: list[int32], big: bool) -> int32:  # tpyc: mir(uncovered /missing or invalid native iteration facts/)
    # a nested comprehension's condition grows the OUTER source
    return len([[w for w in [1, 2] if big and grow(xs)] for v in xs])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)


def gen_body(xs: list[int32], big: bool) -> Iterator[int32]:  # tpyc: mir(conflict /replacement/)
    # generator body
    yield len([v for v in xs if big and grow(xs)])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/) warning(/may move the elements of 'xs'. but the iteration over 'xs' still walks them/)


async def async_body(xs: list[int32], big: bool) -> int32:
    # async body
    return len([v for v in xs if big and grow(xs)])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)


def nested_def(xs: list[int32], big: bool) -> int32:
    # nested def, the source is a capture
    def inner() -> int32:
        return len([v for v in xs if big and xs.pop() > 0])  # tpyc: warning(/Mutation of 'xs' while iterating/)
    return inner()


def main() -> None:
    seed = [1, 2]
    print("ctor:", Bag(seed, False).n)
    b = Bag(seed, False)
    print("method:", b.run(False))
    print("direct:", direct([1, 2, 3], False))
    print("element:", element([1, 2, 3], False))
    print("set_comp:", set_comp([1, 2, 2], False))
    print("dict_comp:", dict_comp([1, 2], False))
    print("dict_int_key:", dict_int_key([1, 2], False))
    print("dict_int_key_for:", dict_int_key_for([1, 2], False))
    print("dict_str_key:", dict_str_key(["a", "b"], False))
    print("dict_str_key_for:", dict_str_key_for(["a", "b"], False))
    print("set_source:", set_source({1, 2}, False))
    print("dict_view:", dict_view({1: 10, 2: 20}, False))
    print("call_source:", call_source([1, 2], False))
    print("nested_comp:", nested_comp([1, 2], False))
    print("gen_body:", sum(gen_body([1, 2], False)))
    print("async_body:", asyncio.run(async_body([1, 2], False)))
    print("nested_def:", nested_def([1, 2], False))
