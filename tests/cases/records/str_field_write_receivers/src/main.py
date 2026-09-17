# A `str` / `bytes` FIELD write lands at six receivers: a name, `self`, a
# nested field, a loop variable, an unproven Optional name and an unproven
# Optional over a field -- each measured to DEMOTE a live view of the written
# field to an owned copy -- plus a user `__getitem__` element, which does NOT
# demote and is admitted only because master's write gate admits it
# (BUGS.md#field-view-escape-needs-place). The `Ptr`, tuple-element,
# container-element, borrow-returning-call and `@property`-getter receivers
# are rejected at the write (own `error_` cases, and the workaround is to bind
# the element / call / getter result to a local first, which this case also
# exercises); the `int32` field beside each write keeps the full ladder,
# because no view borrows a scalar. One section per position; each writes
# through the nested receiver and reads back through the ROOT, so a write into
# a copied element would print the old text.
from typing import Iterator

from tpy import int32
from tplib import Box


class Inner:
    name: str
    tag: bytes
    count: int32

    def __init__(self, name: str) -> None:
        self.name = name
        self.tag = b"t0"
        self.count = 0


class Mid:
    inner: Inner

    def __init__(self) -> None:
        self.inner = Inner("mid")


class Outer:
    mid: Mid
    inner: Inner
    rows: list[Inner]
    table: dict[str, Inner]

    def __init__(self) -> None:
        self.mid = Mid()
        self.inner = Inner("inner")
        self.rows = [Inner("row0")]
        self.table = {"k": Inner("tab")}

    @property
    def held(self) -> Inner:
        return self.inner

    # method position: the nested receivers off `self`
    def write_through_self(self) -> None:
        self.inner.name = "m-nested"        # tpyc: ok
        self.rows[0].count = 5              # the scalar keeps the element rung
        self.mid.inner.tag = b"m-hops"      # tpyc: ok


class Ctor:
    inner: Inner

    # constructor position: the write is demoted out of the member-init list
    def __init__(self) -> None:
        self.inner = Inner("ctor")
        self.inner.name = "c-nested"        # tpyc: ok


class Guard:
    def __enter__(self) -> None:
        pass

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def read_root(o: Outer) -> None:
    # Reads travel a different path than the writes did, so an element copy
    # would show up here as the pre-write text.
    for r in o.rows:
        print("  row:", r.name, r.count)
    for k in o.table:
        print("  tab:", o.table[k].name)
    print("  nested:", o.inner.name, o.inner.tag.decode())
    print("  hops:", o.mid.inner.name)


def free_function() -> None:
    o = Outer()
    o.inner.name = "f-nested"               # tpyc: ok
    o.inner.tag = b"t2"                     # tpyc: ok
    o.inner.count = 7                       # the scalar baseline beside it
    o.mid.inner.name = "f-hops"             # tpyc: ok
    o.rows[0].count = 4                     # scalar through an element
    o.table["k"].count = 6                  # ... and through a dict element
    print("free:")
    read_root(o)


def value_sources() -> None:
    o = Outer()
    s = "f-view"
    # The value side is unchanged by the receiver: a view NAME still assigns
    # bare and a concat still assigns its own render.
    o.inner.name = s                        # tpyc: ok
    o.mid.inner.name = o.mid.inner.name + "!"   # tpyc: ok
    print("values:", o.inner.name, o.mid.inner.name)


def local_element_receivers() -> None:
    rows = [Inner("a"), Inner("b")]
    table = {"k": Inner("t")}
    # the element receiver's workaround: bind the element, then write through
    # the binding (a borrow of the element, so the container sees the write)
    r1 = rows[1]
    r1.name = "l-elem"                      # tpyc: ok
    r1.tag = b"l-tag"                       # tpyc: ok
    t = table["k"]
    t.name = "l-dict"                       # tpyc: ok
    for r in rows:
        # loop-variable receiver: the binding aliases the element
        r.count = 3
        r.name = r.name + "+"               # tpyc: ok
    print("locals:", rows[0].name, rows[1].name, rows[1].tag.decode(),
          table["k"].name, rows[0].count)


def property_receiver_workaround() -> None:
    # the getter receiver's workaround: bind the getter result, then write
    # through the binding -- it borrows the returned record, so the owner sees
    # the write and a view held off the binding is demoted at it
    o = Outer()
    r = o.held
    v = r.name
    r.name = "p-getter-replacement-long-enough-to-reallocate"   # tpyc: ok
    print("property:", v, o.inner.name)


def box_receiver() -> None:
    b = Box(Inner("boxed"))
    # a borrow-returning call: the workaround binds the borrow first
    m = b.get()
    m.name = "b-call"                       # tpyc: ok
    b.get().count = 2                       # the scalar keeps the call rung
    print("box:", b.get().name, b.get().count)


def held_view_demotion() -> None:
    # The reason the rejected receivers are rejected, from the other side: at
    # an ADMITTED receiver a live view of the written field is demoted to an
    # owned copy, so the pre-write text survives the write exactly as
    # CPython's rebind does.
    o = Outer()
    m = o.inner
    v = m.name
    o.inner.name = "d-nested-replacement-long-enough-to-reallocate"  # tpyc: warning(/while borrowed/)
    print("demote nested:", v, o.inner.name)
    rows = [Inner("held")]
    for r in rows:
        w = r.name
        r.name = "d-loop-replacement-long-enough-to-reallocate"      # tpyc: ok
        print("demote loop:", w, r.name)


def gen(o: Outer) -> Iterator[int32]:
    # generator position: the receiver chain is rebuilt from the frame slot
    o.inner.name = "g-nested"               # tpyc: ok
    o.rows[0].count = 8                     # scalar through an element
    yield o.inner.count


def generator_position() -> None:
    o = Outer()
    for v in gen(o):
        print("gen:", v, o.inner.name, o.rows[0].count)


def with_body() -> None:
    o = Outer()
    with Guard():
        o.inner.name = "w-nested"           # tpyc: ok
    print("with:", o.inner.name)


def try_finally() -> None:
    o = Outer()
    try:
        o.inner.tag = b"t-elem"             # tpyc: ok
    finally:
        o.mid.inner.name = "t-hops"         # tpyc: ok
    print("try:", o.inner.tag.decode(), o.mid.inner.name)


def match_arm(n: int32) -> None:
    o = Outer()
    match n:
        case 1:
            o.inner.name = "match-one"      # tpyc: ok
        case _:
            o.inner.name = "match-other"
    print("match:", o.inner.name)


def nested_def() -> None:
    def inner_write(o: Outer) -> None:
        o.inner.name = "n-nested"           # tpyc: ok

    o = Outer()
    inner_write(o)
    print("nested_def:", o.inner.name)


# module level: the receiver is a global pointer slot, so the write travels a
# different path than the function-local one above
MODULE_OUTER = Outer()
MODULE_OUTER.inner.name = "mod-nested"      # tpyc: ok
MODULE_OUTER.rows[0].count = 9              # scalar through an element
MODULE_OUTER.mid.inner.tag = b"mod-hops"    # tpyc: ok


def main() -> None:
    print("module:", MODULE_OUTER.inner.name, MODULE_OUTER.rows[0].count,
          MODULE_OUTER.mid.inner.tag.decode())
    free_function()
    value_sources()
    local_element_receivers()
    property_receiver_workaround()
    box_receiver()
    held_view_demotion()
    o = Outer()
    o.write_through_self()
    print("method:", o.inner.name, o.rows[0].count,
          o.mid.inner.tag.decode())
    print("ctor:", Ctor().inner.name)
    generator_position()
    with_body()
    try_finally()
    match_arm(1)
    nested_def()


main()
