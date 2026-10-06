# A dict or set literal bound to an unannotated local decides its numeric
# leaves -- key, value, element, tuple members, rows -- as a list's do.
import asyncio
from enum import Enum
from typing import Iterator, Optional

from tpy import int8, int32, int64, error_return, ReturnException, Own


class NotFound(Exception, ReturnException):
    pass


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


class Box:
    d: dict[str, int64]

    def __init__(self) -> None:
        self.d = {}


def wide() -> int64:
    return 1099511627776


def take64(d: dict[str, int64]) -> None:
    d["z"] = 5


def takeset64(s: set[int64]) -> None:
    s.add(9)


def a8() -> int8:
    return 100


def take_opt(d: Optional[dict[str, int64]], s: Optional[set[int64]]) -> None:
    if d is not None:
        d["o"] = 9
    if s is not None:
        s.add(9)


# free function -- a value and an element widen; a read after follows
def free_fn() -> None:
    d = {"a": 1}  # tpyc: type(dict[str, int64])
    d["b"] = wide()  # tpyc: ok
    s = {1}  # tpyc: type(set[int64])
    s.add(wide())  # tpyc: ok
    print("free:", d["b"], d, sorted(s))


class Holder:
    total: int64

    # constructor
    def __init__(self) -> None:
        d = {"a": 1}  # tpyc: type(dict[str, int64])
        d["b"] = wide()  # tpyc: ok
        s = {2}  # tpyc: type(set[int64])
        s.add(wide())  # tpyc: ok
        self.total = d["b"] + len(s)

    # method
    def run(self) -> None:
        d = {"a": 1}  # tpyc: type(dict[str, int64])
        d["b"] = wide()  # tpyc: ok
        s = {3}  # tpyc: type(set[int64])
        s.add(wide())  # tpyc: ok
        print("method:", d, sorted(s))


# generator
def gen_pos() -> Iterator[int64]:
    d = {"a": 1}  # tpyc: type(dict[str, int64])
    d["b"] = wide()  # tpyc: ok
    s = {4}  # tpyc: type(set[int64])
    s.add(wide())  # tpyc: ok
    yield d["b"]
    yield len(s)


# async
async def async_pos() -> int64:
    await asyncio.sleep(0)
    d = {"a": 1}  # tpyc: type(dict[str, int64])
    d["b"] = wide()  # tpyc: ok
    s = {5}  # tpyc: type(set[int64])
    s.add(wide())  # tpyc: ok
    return d["b"] + len(s)


# comprehension body -- reads the widened containers
def comp_pos() -> None:
    d = {"a": 1}  # tpyc: type(dict[str, int64])
    d["b"] = wide()  # tpyc: ok
    s = {6}  # tpyc: type(set[int64])
    s.add(wide())  # tpyc: ok
    print("comp:", [v + 1 for v in d.values()], sorted([e - 1 for e in s]))


# closure -- the nested function's own containers
def closure_pos() -> None:
    def inner() -> int64:
        d = {"a": 1}  # tpyc: type(dict[str, int64])
        d["b"] = wide()  # tpyc: ok
        s = {7}  # tpyc: type(set[int64])
        s.add(wide())  # tpyc: ok
        return d["b"] - len(s)

    print("closure:", inner())


# with body
def with_pos() -> None:
    with Guard(8) as tag:
        d = {"a": 1}  # tpyc: type(dict[str, int64])
        d["b"] = wide()  # tpyc: ok
        s = {1}  # tpyc: type(set[int64])
        s.add(wide())  # tpyc: ok
        s.add(tag)
        print("with:", d, sorted(s))


# try / finally
def try_pos() -> None:
    d = {"a": 1}  # tpyc: type(dict[str, int64])
    s = {10}  # tpyc: type(set[int64])
    try:
        d["b"] = wide()  # tpyc: ok
        s.add(wide())  # tpyc: ok
    finally:
        print("try:", d, sorted(s))


# @error_return body
@error_return(NotFound)
def er_pos(ok: bool) -> int64:
    d = {"a": 1}  # tpyc: type(dict[str, int64])
    s = {11}  # tpyc: type(set[int64])
    if not ok:
        raise NotFound
    d["b"] = wide()  # tpyc: ok
    s.add(wide())  # tpyc: ok
    return d["b"] + len(s)


# match arm
def match_pos(c: Color) -> None:
    match c:
        case Color.RED:
            d = {"a": 1}  # tpyc: type(dict[str, int64])
            d["b"] = wide()  # tpyc: ok
            s = {12}  # tpyc: type(set[int64])
            s.add(wide())  # tpyc: ok
            print("match:", d, sorted(s))
        case _:
            print("match: other")


# the worked example: a read before the widening follows it, a typed
# container confirms it and its store is visible, a default only handed
# back fits, and update stores the literal's values
def worked_pos() -> None:
    d = {"a": 1}  # tpyc: type(dict[str, int64])
    d["b"] = wide()  # tpyc: ok
    n = d["a"]  # tpyc: type(int64)
    take64(d)  # tpyc: ok
    print("worked:", n, d.get("c", 0), len(d), d)
    d.update({"r": 2})  # tpyc: ok
    print("worked:", d)


# an empty dict and an empty set are seeded by their first store
def empty_seed_pos() -> None:
    f = {}  # tpyc: type(dict[str, int64])
    f["a"] = 1
    f["b"] = wide()  # tpyc: ok
    s = set()  # tpyc: type(set[int64])
    s.add(1)
    s.add(wide())  # tpyc: ok
    print("empty_seed:", f, sorted(s))


# two empty literals are independent
def independent_pos() -> None:
    m = {}  # tpyc: type(dict[str, int32])
    m2 = {}  # tpyc: type(dict[str, int64])
    m["a"] = 1
    m2["a"] = wide()  # tpyc: ok
    print("independent:", m, m2)


# a list-literal value is a row: one row element for every value; a name
# for a row, written after the widening, is printed through the dict
def rows_pos() -> None:
    g = {"a": [1]}  # tpyc: type(dict[str, list[int64]])
    g["a"].append(wide())  # tpyc: ok
    g["b"] = [wide()]  # tpyc: ok
    row = g["a"]  # tpyc: type(list[int64])
    row.append(7)
    print("rows:", g)


# a set's element widens; |= stores the literal's elements; a typed set
# decides it and its store is visible
def set_pos() -> None:
    s = {1}  # tpyc: type(set[int64])
    s.add(wide())  # tpyc: ok
    s |= {7}  # tpyc: ok
    takeset64(s)  # tpyc: ok
    print("set:", sorted(s))


# an inserting key widens the key; a literal looked up adapts
def key_pos() -> None:
    k = {1: "x"}  # tpyc: type(dict[int64, str])
    k[wide()] = "y"  # tpyc: ok
    print("key:", k[1], 1 in k, len(k))


# tuple keys widen member by member
def tuple_key_pos() -> None:
    t = {(1, 2): "x"}  # tpyc: type(dict[tuple[int64, int32], str])
    t[(wide(), 3)] = "y"  # tpyc: ok
    print("tuple_key:", len(t), t[(1, 2)])


# a dict of dicts: the leaves at depth two widen; a name for the inner
# dict, written after the widening, is printed through the outer one
def nested_dict_pos() -> None:
    dd = {"a": {"x": 1}}  # tpyc: type(dict[str, dict[str, int64]])
    dd["a"]["y"] = wide()  # tpyc: ok
    inner = dd["a"]  # tpyc: type(dict[str, int64])
    inner["z"] = 3
    print("nested_dict:", dd)


# a dict inside a list
def dict_in_list_pos() -> None:
    ls = [{"a": 1}]  # tpyc: type(Array[dict[str, int64], 1])
    ls[0]["b"] = wide()  # tpyc: ok
    print("dict_in_list:", ls)


# an optional read, then a widening: the read follows the cell
def optional_read_pos() -> None:
    d = {"a": 1}  # tpyc: type(dict[str, int64])
    v = d.get("a")
    d["w"] = wide()  # tpyc: ok
    print("optional_read:", v, d["w"])


# update from a source decided narrower: the source keeps its width, the
# runtime converts its entries
def update_narrower_pos() -> None:
    d = {"a": 1}  # tpyc: type(dict[str, int64])
    other = {"b": 1}  # tpyc: type(dict[str, int32])
    d.update(other)  # tpyc: ok
    d["c"] = wide()  # tpyc: ok
    print("update_narrower:", d, other)


# a field of a typed dict decides the dict; it is copied into the field
# (the documented container copy), so the field is printed before the
# local is written again
def field_ctx_pos() -> None:
    b = Box()
    d = {"a": 1}  # tpyc: type(dict[str, int64])
    b.d = d  # tpyc: warning(/copies dict\[str, int64\] into field/)
    print("field_ctx:", b.d)
    d["b"] = wide()  # tpyc: ok
    print("field_ctx:", d)


# an Own return slot decides the dict
def own_return_pos() -> Own[dict[str, int64]]:
    d = {"a": 1}  # tpyc: type(dict[str, int64])
    d["b"] = 2  # tpyc: ok
    return d


# a select of two dicts decided alike
def select_pos(c: bool) -> None:
    d1 = {"a": 1}  # tpyc: type(dict[str, int32])
    d2 = {"b": 2}  # tpyc: type(dict[str, int32])
    z = d1 if c else d2
    print("select:", z)


# one local, one element type: a rebinding literal widens it
def rebind_pos() -> None:
    dd2 = {"a": 1}  # tpyc: type(dict[str, int64])
    dd2 = {"b": wide()}  # tpyc: ok
    print("rebind:", dd2)


# truth, len, print and membership need no width
def sinks_pos() -> None:
    d = {"a": 1}  # tpyc: type(dict[str, int64])
    s = {1}  # tpyc: type(set[int64])
    if d and 2 not in s:
        print("sinks:", len(d), d, 1 in s)
    d["b"] = wide()  # tpyc: ok
    s.add(wide())  # tpyc: ok
    print("sinks:", d, sorted(s))


# setdefault stores its key and value; pop hands back the value
def methods_pos() -> None:
    d = {"a": 1}  # tpyc: type(dict[str, int64])
    d.setdefault("b", wide())  # tpyc: ok
    v = d.pop("a")  # tpyc: type(int64)
    print("methods:", v, d, d.pop("x", 4))


# iteration decides from what the dict holds so far: after the widening
# it reads the widened values
def loop_pos() -> None:
    d = {"a": 1}  # tpyc: type(dict[str, int64])
    d["b"] = wide()  # tpyc: ok
    for k, v in d.items():
        print("loop:", k, v)


# a typed container the dict meets first, then a store that fits it
def ctx_then_fits_pos() -> None:
    d = {"a": 1}  # tpyc: type(dict[str, int64])
    take64(d)
    d["c"] = wide()  # tpyc: ok
    print("ctx_then_fits:", d)


# a set updated from a set decided narrower: the runtime converts its
# elements
def set_update_narrower_pos() -> None:
    s = {1}  # tpyc: type(set[int64])
    o = {2}  # tpyc: type(set[int32])
    s.update(o)  # tpyc: ok
    s.add(wide())  # tpyc: ok
    print("set_update_narrower:", sorted(s), sorted(o))


# tuple keys looked up with `in`: a literal tuple adapts to the key
def tuple_in_pos() -> None:
    s = {(1, 2)}  # tpyc: type(set[tuple[int32, int32]])
    d = {(1, 2): "x"}  # tpyc: type(dict[tuple[int32, int32], str])
    print("tuple_in:", (1, 2) in s, (3, 4) in s, (1, 2) in d, (2, 1) in d)  # tpyc: ok


# update from a dict whose rows and inner dicts are literals of their own:
# they are linked to the receiver's, so both hold one width. The entries
# are copied (the documented container copy), so nothing is written to
# the source afterwards
def update_rows_pos() -> None:
    g = {"a": [1]}  # tpyc: type(dict[str, Array[int64, 1]])
    g["c"] = [wide()]
    h = {"z": [3]}  # tpyc: type(dict[str, Array[int64, 1]])
    g.update(h)  # tpyc: warning(/copies list\[int64\] elements/)
    dd = {"a": {"x": 1}}  # tpyc: type(dict[str, dict[str, int64]])
    dd["a"]["y"] = wide()
    src = {"b": {"z": 1}}  # tpyc: type(dict[str, dict[str, int64]])
    dd.update(src)  # tpyc: warning(/copies dict\[str, int64\] elements/)
    print("update_rows:", g, h, dd, src)


# an Optional parameter takes a written dict and set after a widening, and
# an empty pair its first stores seeded; its writes are visible here
def optional_param_pos() -> None:
    d = {"a": 1}  # tpyc: type(dict[str, int64])
    d["b"] = wide()
    s = {1}  # tpyc: type(set[int64])
    s.add(wide())
    take_opt(d, s)  # tpyc: ok
    e = {}  # tpyc: type(dict[str, int64])
    e["a"] = 1
    es = set()  # tpyc: type(set[int64])
    es.add(2)
    take_opt(e, es)  # tpyc: ok
    print("optional_param:", d, sorted(s), e, sorted(es))


# a name bound to the dict after its widening is the same dict
def alias_pos() -> None:
    d = {"a": 1}  # tpyc: type(dict[str, int64])
    d["b"] = wide()
    # (no type() pin here: BUGS.md#dict-set-alias-decl-type-stays-pending)
    e = d
    e["c"] = 3  # tpyc: ok
    print("alias:", d)


# setdefault inserts its key: a wider one widens the key
def setdefault_key_pos() -> None:
    k = {1: "x"}  # tpyc: type(dict[int64, str])
    k.setdefault(wide(), "y")  # tpyc: ok
    print("setdefault_key:", len(k), k[1])


# a narrower typed default and key are converted at the leaf they are looked
# up at; a wider store afterwards still widens it
def narrow_lookup_pos() -> None:
    d = {"a": 1}  # tpyc: type(dict[str, int64])
    print("narrow_lookup:", d.get("c", a8()))  # tpyc: ok
    k = {100: 5}  # tpyc: type(dict[int64, int32])
    print("narrow_lookup:", k.get(a8()))  # tpyc: ok
    d["b"] = wide()  # tpyc: ok
    k[wide()] = 6  # tpyc: ok
    print("narrow_lookup:", d, len(k))


# a set inside a dict widens through the dict's read
def dict_of_sets_pos() -> None:
    d = {"a": {1}}  # tpyc: type(dict[str, set[int64]])
    d["a"].add(wide())  # tpyc: ok
    print("dict_of_sets:", len(d["a"]))


# an empty dict's first evidence is a setdefault, an empty set's an update
# from a set literal; a wider add afterwards widens the set
def first_evidence_pos() -> None:
    d = {}  # tpyc: type(dict[str, str])
    d.setdefault("a", "x")  # tpyc: ok
    s = set()  # tpyc: type(set[int64])
    s.update({1, 2})  # tpyc: ok
    s.add(wide())  # tpyc: ok
    print("first_evidence:", d, sorted(s))


# `del d[k]` looks its key up: a fitting key leaves the cells open, and a
# wider store afterwards widens them
def del_lookup_pos() -> None:
    k = {1: "x", 2: "y"}  # tpyc: type(dict[int64, str])
    del k[1]  # tpyc: ok
    k[wide()] = "z"  # tpyc: ok
    print("del_lookup:", k)


# `in` with a narrower typed operand is looked up at the open key / element:
# a wider store afterwards still widens them, and the same lookups follow
def narrow_in_pos() -> None:
    d = {100: "x", 2: "y"}  # tpyc: type(dict[int64, str])
    s = {1, 2}  # tpyc: type(set[int64])
    print("narrow_in:", a8() in d, a8() in s)  # tpyc: ok
    d[wide()] = "z"  # tpyc: ok
    s.add(wide())  # tpyc: ok
    print("narrow_in:", a8() in d, a8() in s, d, sorted(s))


def main() -> None:
    free_fn()
    h = Holder()
    print("ctor:", h.total)
    h.run()
    print("gen:", list(gen_pos()))
    print("async:", asyncio.run(async_pos()))
    comp_pos()
    closure_pos()
    with_pos()
    try_pos()
    try:
        print("error_return:", er_pos(True))
        er_pos(False)
    except NotFound:
        print("error_return: raised")
    match_pos(Color.RED)
    worked_pos()
    empty_seed_pos()
    independent_pos()
    rows_pos()
    set_pos()
    key_pos()
    tuple_key_pos()
    nested_dict_pos()
    dict_in_list_pos()
    optional_read_pos()
    update_narrower_pos()
    field_ctx_pos()
    print("own_return:", own_return_pos())
    select_pos(True)
    rebind_pos()
    sinks_pos()
    methods_pos()
    loop_pos()
    ctx_then_fits_pos()
    set_update_narrower_pos()
    tuple_in_pos()
    update_rows_pos()
    optional_param_pos()
    alias_pos()
    setdefault_key_pos()
    narrow_lookup_pos()
    dict_of_sets_pos()
    first_evidence_pos()
    del_lookup_pos()
    narrow_in_pos()


main()
