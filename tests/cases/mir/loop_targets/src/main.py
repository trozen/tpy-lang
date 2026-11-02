# MIR over for-each loops: a loop variable that copies its element (a str key, an int), a loop over
# a str / bytes / String / BytesView binding, field or parameter, a loop over a list slice or a Span,
# the iteration remedies, the bodies where the str-loop hazard stays silent, and the free math / ord
# stubs. Hazard sections are defined but not called: each diverges from CPython.
import math
from typing import Iterator
from tpy import BytesView, String, int32

G = "a-global-string-longer-than-the-small-buffer"


class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Words:
    d: dict[str, int32]
    s: str

    # __init__: a copied key walks the dict and a cursor walks the str field
    def __init__(self, s: str) -> None:  # tpyc: mir(covered)
        self.d = {"a": 1, "b": 2}
        self.s = s
        n = 0
        for k in self.d:  # tpyc: ok
            n += len(k)
        for c in self.s:  # tpyc: ok
            n += 1
        print("init:", n)

    # method, hazard: popping a key the copied loop variable holds, under the walk of the field
    def drop_all(self) -> None:  # tpyc: mir(conflict /replacement/)
        for k in self.d:
            self.d.pop(k)  # tpyc: warning(/Mutation of 'self.d'/) warning(/may move the elements of 'self.d'/)

    # method, hazard: replacing the str field the loop walks frees its buffer
    def respell(self) -> None:  # tpyc: mir(conflict /replacement/)
        for c in self.s:
            self.s = "q"  # tpyc: warning(/Mutation of 'self.s'/) warning(/frees the old value of 'self.s'/)

    # leaf remedy, clean: the str field walked through a copy under another name
    def field_copied(self) -> str:  # tpyc: mir(covered)
        u = self.s
        for c in u:  # tpyc: ok
            self.s = self.s + "x"  # tpyc: ok
        return self.s + "|" + u

    # method, clean: the copied key outlives the pop; the field is reassigned after the loop
    def keys_then_pop(self) -> int32:  # tpyc: mir(covered)
        n = 0
        for k in self.d:  # tpyc: ok mir_copy(k)
            k = k + "!"  # tpyc: ok
            n += len(k)
        for c in self.s:
            n += 1
        self.s = "done"
        return n


# free function, hazard: popping the key the loop variable copied, under the dict's walk
def pop_keys(d: dict[str, int32]) -> None:  # tpyc: mir(conflict /replacement/)
    for k in d:  # tpyc: mir_copy(k)
        d.pop(k)  # tpyc: warning(/Mutation of 'd'/) warning(/may move the elements of 'd'/)


# free function, hazard: a str local reassigned inside the loop over it frees the buffer it walks
def grow_str(s: str) -> None:  # tpyc: mir(conflict /replacement/)
    t = s + "!"
    for c in t:
        t = t + "x"  # tpyc: warning(/frees the old value of 't', but the iteration over 't' still walks it/)
    print(t)


# free function, hazard: a reassigned str parameter is a local copy the loop walks
def respell_param(s: str) -> None:  # tpyc: mir(conflict /replacement/)
    for c in s:
        s = "Z"  # tpyc: warning(/frees the old value of 's'.*make the change after the loop/)
    print(s)


# free function, hazard: the same for a bytes local
def grow_bytes(b: bytes) -> None:  # tpyc: mir(conflict /replacement/)
    t = b + b"!"
    for x in t:
        t = t + b"x"  # tpyc: warning(/frees the old value of 't'/)
    print(t)


# free function, hazard: a list slice walks its receiver's elements, which the append may move
def grow_from_slice(items: list[int32]) -> None:  # tpyc: mir(conflict /replacement/)
    for x in items[0:2]:
        items.append(x)  # tpyc: warning(/Mutation of 'items'/) warning(/iteration over 'items\[0:2\]'.*iterate over a copy \('list\(items\[0:2\]\)'\)/)


# nested loops, hazard: the inner loop walks the copied key, which the body replaces
def nested_respell(d: dict[str, int32]) -> None:  # tpyc: mir(conflict /replacement/)
    for k in d:
        for c in k:
            k = k + "z"  # tpyc: warning(/frees the old value of 'k'/)


# free function, clean: a copied str key reassigned and kept past the pop
def keys_after_pop(d: dict[str, int32]) -> str:  # tpyc: mir(covered)
    last = ""
    for k in d:  # tpyc: ok mir_copy(k)
        k = k + "?"  # tpyc: ok
        last = k
    d.pop("a")
    return last


# free function, clean: a copied int element (C++ `BigInt y = *it`) and a const reference to one
def sum_ints(xs: list[int]) -> int:  # tpyc: mir(covered)
    t: int = 0
    for y in xs:  # tpyc: ok mir_copy(y)
        y = y + 1
        t = t + y
    for z in xs:  # tpyc: ok
        t = t + z
    return t


# free function, clean: a set of str walked, each element copied into the reassigned loop variable
def set_keys(ws: set[str]) -> int32:  # tpyc: mir(covered)
    n = 0
    for w in ws:  # tpyc: ok mir_copy(w)
        w = w + "."
        n += len(w)
    return n


# free function, clean: counting the characters of an owned str and rebuilding another
def count_chars(s: str) -> int32:  # tpyc: mir(covered)
    t = s + "!"
    n = 0
    u = ""
    for c in t:  # tpyc: ok
        n += 1
        u = u + c  # tpyc: ok
    print("count_chars:", u)
    return n


# free function, clean: reseating a view local frees nothing; the loop keeps the old buffer
def reseat_view(s: str, r: str) -> int32:  # tpyc: mir(covered)
    t = s  # tpyc: type(StrView)
    n = 0
    for c in t:  # tpyc: ok
        t = r  # tpyc: ok
        n += 1
    return n


# free function, clean: a reassigned str parameter walked, then reassigned after the loop
def param_after(s: str) -> str:  # tpyc: mir(covered)
    n = 0
    for c in s:  # tpyc: ok
        n += 1
    s = s + str(n)
    return s


# free function, clean: the bytes of a parameter summed
def sum_bytes(b: bytes) -> int32:  # tpyc: mir(covered)
    n = 0
    for x in b:  # tpyc: ok
        n += 1
    return n


# free function, clean: a slice read only
def sum_slice(items: list[int32]) -> int32:  # tpyc: mir(covered)
    n = 0
    for x in items[1:3]:  # tpyc: ok
        n += x
    return n


# generator, clean: the loop ends before the yield
def lengths(d: dict[str, int32]) -> Iterator[int32]:  # tpyc: mir(covered)
    n = 0
    for k in d:  # tpyc: ok
        n += len(k)
    yield n


# free stubs: math functions and ord summarize as pure calls
def maths(x: float, s: str) -> float:  # tpyc: mir(covered)
    i = math.floor(x) + math.ceil(x)  # tpyc: ok
    j: int32 = math.trunc(x)  # tpyc: ok
    print("maths:", i, j, ord(s), math.isnan(x))  # tpyc: ok
    return math.sqrt(x) + math.atan2(x, 1.0) + math.fabs(x) + math.pow(x, 2.0)  # tpyc: ok


# free function, hazard: a String parameter reassigned in the loop over it
def string_hazard(w: String) -> None:  # tpyc: mir(conflict /replacement/)
    for c in w:
        w = w + "x"  # tpyc: warning(/frees the old value of 'w'.*iterate over a copy under another name/)
    print(w)


# free function, clean: the String walked read only
def string_clean(w: String) -> int32:  # tpyc: mir(covered)
    n = 0
    for c in w:  # tpyc: ok
        n += 1
    return n


# free function, hazard: a BytesView walks an owned bytes local the body reassigns
def bytes_view_hazard(data: bytes) -> None:  # tpyc: mir(conflict /replacement/)
    t = data + b"!"
    v: BytesView = t
    for x in v:
        t = t + b"x"  # tpyc: warning(/Mutation of 't'/) warning(/frees the old value of 't', but the iteration over 'v'/)
    print(t)


# free function, clean: a BytesView parameter walked read only
def bytes_view_clean(v: BytesView) -> int32:  # tpyc: mir(covered)
    n = 0
    for x in v:  # tpyc: ok
        n += 1
    return n


# remedy, clean: the remedy the str warnings offer -- a copy under another name is owned storage
def copy_under_name(s: str) -> str:  # tpyc: mir(covered)
    t = s + "!"
    u = t  # tpyc: type(String)
    for c in u:  # tpyc: ok
        t = t + "x"  # tpyc: ok
    return t + u


# remedy, hazard: a Span local walked while its list grows; the remedy is list(...), not copy(...)
def span_hazard(items: list[int32]) -> None:  # tpyc: mir(conflict /replacement/)
    sp = items[0:2]
    for x in sp:
        items.append(x)  # tpyc: warning(/Mutation of 'items'/) warning(/iterate over a copy \('list\(sp\)'\)/)


# remedy, hazard: a dict values view walked while a key is popped
def values_hazard(d: dict[str, int32]) -> None:  # tpyc: mir(conflict /replacement/)
    for v in d.values():
        d.pop("a")  # tpyc: warning(/Mutation of 'd'/) warning(/iterate over a copy \('list\(d.values\(\)\)'\)/)


# remedy, clean: the slice remedy compiles and walks a list of its own (MIR does not lower a loop
# over a call result)
def slice_copied(items: list[int32]) -> int32:  # tpyc: mir(uncovered /container source needs a place/)
    for x in list(items[0:2]):  # tpyc: ok
        items.append(x)  # tpyc: ok
    return len(items)


# element store of a global str, no loop: an owned-leaf element copies the global (MIR used to crash)
def store_global(a: list[str]) -> None:  # tpyc: mir(covered)
    a[0] = G  # tpyc: ok mir_copy(a[elements])


# silent, a literal into an owned String: the coercion is refused first, so the str-loop hazard is
# unwarned (BUGS.md#str-loop-walks-reassigned-name)
def literal_respell(s: str) -> None:  # tpyc: mir(uncovered /conversion aliases its source/)
    t = s + "!"
    for c in t:
        t = "Z"
    print(t)


# silent, generator: an owned str local looped before the yield is a hoisted frame local MIR refuses
# (BUGS.md#str-loop-walks-reassigned-name)
def gen_respell(s: str) -> Iterator[int32]:  # tpyc: mir(uncovered /unsupported hoisted value/)
    t = s + "!"
    for c in t:
        t = t + "x"
    yield 1


# silent, nested def: the outer body is refused at the def, the inner one is not lowered
# (BUGS.md#str-loop-walks-reassigned-name)
def outer_respell(s: str) -> None:  # tpyc: mir(uncovered /unsupported statement/)
    def inner() -> None:
        t = s + "!"
        for c in t:
            t = t + "x"
        print(t)
    inner()


# silent, a body MIR refuses for an unrelated reason: the walrus `if` carries the THIR metadata
# MIR refuses (BUGS.md#str-loop-walks-reassigned-name)
def walrus_respell(s: str) -> None:  # tpyc: mir(uncovered /unsupported metadata: cpp_type/)
    t = s + "!"
    for c in t:
        t = t + "x"
    if (n := len(t)) > 0:
        print(n)


# global, verdict pinned: a loop over a module-level str is not a named place MIR walks yet
def global_loop() -> int32:  # tpyc: mir(uncovered /leaf iteration needs a named place/)
    n = 0
    for c in G:
        n += 1
    return n


# remedy, hazard: a slice of RECORD elements; `list(...)` would copy them (CPython's is shallow), so
# the remedy offers none
def record_slice_hazard(xs: list[Cell]) -> None:  # tpyc: mir(conflict /replacement/)
    for p in xs[0:1]:
        xs.append(Cell(9))  # tpyc: warning(/Mutation of 'xs'/) warning(/iteration over 'xs\[0:1\]' still walks them and continues on line \d+; make the changes after the loop$/) warning(/'p' still refers to them/)
        p.n += 1


# leaf remedy, clean: a reassigned str parameter walked through a copy under another name
def param_copied(s: str) -> str:  # tpyc: mir(covered)
    u = s  # tpyc: type(str)
    for c in u:  # tpyc: ok
        s = s + "x"  # tpyc: ok
    return s + "|" + u


# leaf remedy, clean: a bytes local walked through a copy under another name
def bytes_copied(data: bytes) -> bytes:  # tpyc: mir(covered)
    t = data + b"!"
    u = t
    for x in u:  # tpyc: ok
        t = t + b"x"  # tpyc: ok
    return t + b"|" + u


# leaf remedy section: a BytesView over an owned local, its buffer copied under another name first
def view_copied(data: bytes) -> bytes:  # tpyc: mir(covered)
    t = data + b"!"
    v: BytesView = t
    u = bytes(v)
    for x in u:  # tpyc: ok
        # a false sema warning: 'v' is dead here, but sema keeps its loan to the end of the body
        # (BUGS.md#borrow-warning-not-last-use-aware); MIR is silent
        t = t + b"x"  # tpyc: warning(/Mutation of 't' while borrowed/)
    return t + b"|" + u


# Span remedy section: the list(...) copy of a Span of scalars walks a list of its own
def span_list(items: list[int32]) -> int32:  # tpyc: mir(uncovered /container source needs a place/)
    sp = items[0:2]
    for x in list(sp):
        # a false sema warning: list(sp) runs once before the loop and 'sp' is dead after it
        # (BUGS.md#borrow-warning-not-last-use-aware)
        items.append(x)  # tpyc: warning(/Mutation of 'items' while borrowed/)
    return len(items)


# dict values remedy, clean: the list(...) copy of a dict's int values walks a list of its own
def values_list(d: dict[str, int32]) -> int32:  # tpyc: mir(uncovered /container source needs a place/)
    n = 0
    for v in list(d.values()):  # tpyc: ok
        if "a" in d:
            d.pop("a")
        n += v
    return n


# remedy, hazard: a Span of RECORD elements; `list(...)` would copy them, so the remedy offers none
# (a dict values view over records is a THIR reject today, so it has no section)
def record_span_hazard(xs: list[Cell]) -> None:  # tpyc: mir(conflict /replacement/)
    sp = xs[0:1]
    for c in sp:
        xs.append(Cell(9))  # tpyc: warning(/Mutation of 'xs' while borrowed/) warning(/iteration over 'sp' still walks them and continues on line \d+; make the changes after the loop$/)


def main() -> None:
    w = Words("abc")
    print("keys_then_pop:", w.keys_then_pop(), w.s)
    print("keys_after_pop:", keys_after_pop({"a": 1, "b": 2}))
    print("sum_ints:", sum_ints([1, 2, 3]))
    print("set_keys:", set_keys({"x"}))
    n = count_chars("ab")
    print("count_chars:", n)
    print("reseat_view:", reseat_view("abc", "z"))
    print("param_after:", param_after("ab"))
    print("sum_bytes:", sum_bytes(b"abc"))
    print("sum_slice:", sum_slice([1, 2, 3, 4]))
    for m in lengths({"ab": 1, "c": 2}):
        print("lengths:", m)
    r = maths(2.25, "a")
    print("maths:", r)
    print("string_clean:", string_clean(String("ab")))
    print("bytes_view_clean:", bytes_view_clean(b"abc"))
    print("copy_under_name:", copy_under_name("ab"))
    print("slice_copied:", slice_copied([1, 2, 3]))
    names = ["a", "b"]
    store_global(names)
    print("store_global:", names[0])
    print("global_loop:", global_loop())
    words = Words("ab")
    print("field_copied:", words.field_copied())
    print("param_copied:", param_copied("ab"))
    print("bytes_copied:", bytes_copied(b"ab"))
    print("view_copied:", view_copied(b"ab"))
    print("span_list:", span_list([1, 2, 3]))
    print("values_list:", values_list({"a": 1, "b": 2}))


main()
