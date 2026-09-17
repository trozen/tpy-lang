# A container ELEMENT reached through a None-narrowed `Optional[container]`:
# the method-call face (`d["o"].append(3)`), the binding face
# (`vals = d["o"]`), and the element-FIELD faces (`d["o"].rows.append(3)`,
# `d["o"].n += 1`, `d["o"].kids["a"] = 3`, `d["o"].rows[0] = 9`,
# `d["o"].sub = None`), at every position and narrowing form that reaches them.
# Every section mutates the element AFTER the narrowing and reads it back
# through the OUTER container, so a silent copy of the element would print a
# stale value instead of the mutated one.
import asyncio
from typing import Iterator

from tpy import int32


class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1


class Node:
    n: int32
    rows: list[int32]
    kids: dict[str, int32]
    sub: 'Counter | None'

    def __init__(self, n: int32) -> None:
        self.n = n
        self.rows = [n]
        self.kids = {}
        self.sub = None


def take(xs: list[int32]) -> None:
    xs.append(9)


class Guard:
    def __enter__(self) -> int32:
        return 7

    def __exit__(self, kind, value, tb) -> None:
        pass


def method_face() -> None:
    d: dict[str, list[int32]] | None = {"o": [1, 2]}
    if d is not None:
        d["o"].append(3)  # tpyc: ok
        print("method_face", d["o"][2])


def bind_face() -> None:
    d: dict[str, list[int32]] | None = {"o": [1, 2]}
    if d is not None:
        vals = d["o"]  # tpyc: ok
        vals.append(3)
        print("bind_face", d["o"][2], len(vals))


def list_receiver() -> None:
    m: list[list[int32]] | None = [[1, 2]]
    if m is not None:
        m[0].append(3)  # tpyc: ok
        m[0][0] = 9  # the nested-element WRITE face
        print("list_receiver", m[0][0], m[0][2])


def dict_nested_write() -> None:
    d: dict[str, list[int32]] | None = {"o": [1, 2]}
    if d is not None:
        d["o"][0] = 7  # tpyc: ok
        print("dict_nested_write", d["o"][0])


def call_arg() -> None:
    d: dict[str, list[int32]] | None = {"o": [1, 2]}
    if d is not None:
        take(d["o"])  # the callee mutates the element in place
        print("call_arg", d["o"][2])


def for_over_elem() -> None:
    d: dict[str, list[int32]] | None = {"o": [1, 2]}
    if d is not None:
        d["o"].append(3)
        total = 0
        for v in d["o"]:
            total += v
        print("for_over_elem", total)


def record_elem() -> None:
    d: dict[str, Counter] | None = {"o": Counter(1)}
    if d is not None:
        d["o"].bump()  # tpyc: ok
        c = d["o"]  # tpyc: ok
        c.bump()
        d["o"].n = d["o"].n + 1  # the element FIELD write face
        print("record_elem", d["o"].n)


def print_and_len() -> None:
    d: dict[str, list[int32]] | None = {"o": [1, 2]}
    if d is not None:
        d["o"].append(3)
        print("print_and_len", d["o"], len(d["o"]))  # tpyc: ok


def str_elem() -> None:
    d: dict[str, str] | None = {"o": "ab"}
    if d is not None:
        print("str_elem", d["o"].upper())  # tpyc: ok


def set_elem() -> None:
    d: dict[str, set[int32]] | None = {"o": {1, 2}}
    if d is not None:
        d["o"].add(3)  # tpyc: ok
        s = d["o"]
        print("set_elem", len(s))


def dict_elem() -> None:
    d: dict[str, dict[str, int32]] | None = {"o": {"a": 1}}
    if d is not None:
        inner = d["o"]  # tpyc: ok
        inner["b"] = 2
        print("dict_elem", d["o"]["b"])


def elif_form() -> None:
    d: dict[str, list[int32]] | None = {"o": [1, 2]}
    if d is None:
        print("elif_form none")
    elif len(d) > 0:
        d["o"].append(3)  # tpyc: ok
        print("elif_form", d["o"][2])


def else_form() -> None:
    d: dict[str, list[int32]] | None = {"o": [1, 2]}
    if d is None:
        print("else_form none")
    else:
        d["o"].append(3)  # narrowed on the FALSE leg of `is None`
        print("else_form", d["o"][2])


def while_form() -> None:
    d: dict[str, list[int32]] | None = {"o": [1, 2]}
    n = 0
    while d is not None and n < 2:
        d["o"].append(3)  # tpyc: ok
        n += 1
    print("while_form", n)


def early_return(d: dict[str, list[int32]] | None) -> None:
    if d is None:
        print("early_return none")
        return
    d["o"].append(3)  # narrowed by the guard-return, param receiver
    print("early_return", d["o"][2])


def assert_form() -> None:
    d: dict[str, list[int32]] | None = {"o": [1, 2]}
    assert d is not None
    d["o"].append(3)  # tpyc: ok
    print("assert_form", d["o"][2])


def and_form() -> None:
    d: dict[str, list[int32]] | None = {"o": [1, 2]}
    if d is not None and len(d) > 0:
        d["o"].append(3)  # tpyc: ok
        print("and_form", d["o"][2])


def match_arm() -> None:
    d: dict[str, list[int32]] | None = {"o": [1, 2]}
    tag = 1
    match tag:
        case 1:
            if d is not None:
                d["o"].append(3)  # tpyc: ok
                print("match_arm", d["o"][2])
        case _:
            print("match_arm other")


def try_finally() -> None:
    d: dict[str, list[int32]] | None = {"o": [1, 2]}
    if d is not None:
        try:
            d["o"].append(3)  # tpyc: ok
        finally:
            print("try_finally", d["o"][2])


class Holder:
    d: dict[str, list[int32]] | None
    recs: dict[str, Node] | None
    n: int32

    def __init__(self) -> None:
        self.d = {"o": [1, 2]}
        self.recs = {"o": Node(1)}
        local: dict[str, list[int32]] | None = {"c": [5]}
        self.n = 0
        if local is not None:
            local["c"].append(6)  # the constructor position
            self.n = local["c"][1]

    def method(self) -> None:
        if self.d is not None:
            self.d["o"].append(3)  # a narrowed FIELD receiver
            row = self.d["o"]  # tpyc: ok
            self.d["o"][0] = 9  # the NESTED subscript off a narrowed FIELD
            print("method", row[0], row[2], self.d["o"][0], self.n)

    def elem_field_method(self) -> None:
        # The element-FIELD faces off a narrowed FIELD receiver.
        if self.recs is not None:
            self.recs["o"].rows.append(3)  # tpyc: ok
            self.recs["o"].n += 1  # tpyc: ok
            print("holder_elem_field", self.recs["o"].rows,
                  self.recs["o"].n, len(self.recs["o"].rows))


def elem_field_method() -> None:
    d: dict[str, Node] | None = {"o": Node(1)}
    if d is not None:
        d["o"].rows.append(3)  # tpyc: ok
        print("elem_field_method", d["o"].rows)


def elem_field_aug() -> None:
    d: dict[str, Node] | None = {"o": Node(1)}
    if d is not None:
        d["o"].n += 1  # tpyc: ok
        print("elem_field_aug", d["o"].n)


def elem_field_print_len() -> None:
    d: dict[str, Node] | None = {"o": Node(1)}
    if d is not None:
        d["o"].rows.append(3)
        print("elem_field_print_len", d["o"].rows,  # tpyc: ok
              len(d["o"].rows))


def elem_field_setitem() -> None:
    d: dict[str, Node] | None = {"o": Node(1)}
    if d is not None:
        d["o"].kids["a"] = 3  # tpyc: ok
        print("elem_field_setitem", d["o"].kids["a"])


def elem_field_nested() -> None:
    d: dict[str, Node] | None = {"o": Node(1)}
    if d is not None:
        # the nested element-FIELD subscript, write then read back: the
        # un-narrowed twin is `optional/error_unnarrowed_opt_elem_chain`
        d["o"].rows[0] = 9  # tpyc: ok
        print("elem_field_nested", d["o"].rows[0])  # tpyc: ok


def elem_opt_field_write() -> None:
    node = Node(1)
    node.sub = Counter(5)
    d: dict[str, Node] | None = {"o": node}
    if d is not None:
        d["o"].sub = None  # tpyc: ok -- the element's OPTIONAL field write
        cleared = d["o"]
        print("elem_opt_field_write", cleared.sub is None)


def gen() -> Iterator[int32]:
    d: dict[str, list[int32]] | None = {"o": [1, 2]}
    if d is not None:
        d["o"].append(3)  # tpyc: ok
        for v in d["o"]:
            yield v


def with_body() -> None:
    d: dict[str, list[int32]] | None = {"o": [1, 2]}
    if d is not None:
        with Guard() as g:
            d["o"].append(g)  # tpyc: ok
        print("with_body", d["o"][2])


async def coro() -> None:
    d: dict[str, list[int32]] | None = {"o": [1, 2]}
    if d is not None:
        d["o"].append(3)  # tpyc: ok
        print("coro", d["o"][2])


# module level: the narrowing and the element mutation are top-level
# statements, so the receiver is a global pointer slot
MODULE_D: dict[str, list[int32]] | None = {"o": [1, 2]}
if MODULE_D is not None:
    MODULE_D["o"].append(3)  # tpyc: ok
    print("module_level", MODULE_D["o"][2])


def main() -> None:
    method_face()
    bind_face()
    list_receiver()
    dict_nested_write()
    call_arg()
    for_over_elem()
    record_elem()
    print_and_len()
    str_elem()
    set_elem()
    dict_elem()
    elif_form()
    else_form()
    while_form()
    early_return({"o": [1, 2]})
    early_return(None)
    assert_form()
    and_form()
    match_arm()
    try_finally()
    with_body()
    elem_field_method()
    elem_field_aug()
    elem_field_print_len()
    elem_field_setitem()
    elem_field_nested()
    elem_opt_field_write()
    Holder().method()
    Holder().elem_field_method()
    total = 0
    for v in gen():
        total += v
    print("gen", total)
    asyncio.run(coro())


main()
