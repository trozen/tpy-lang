# A pointer-repr `Optional[record]` LOCAL that may still be None (declared
# None and reassigned only on some path, or never) passed at an
# `Own[record | None]` slot: the owning optional is rebuilt null-safely and
# moved in at the local's last use, at a builtin stub's element slot
# (`append`, `setdefault`), a free function, a user method and the
# `Own[record] | None` spelling -- the render the constructor slot already
# had. A plain `@staticmethod` stands in for every marker-qualified call kind
# (module-qualified, generic static, super): one sink, one render. Every stored element is mutated afterwards through an index read so a
# silent copy would show. A non-last-use occurrence keeps rejecting
# (error_optional_ptr_local_copy_into_own_slot). Not here: iterating the list
# of optionals (BUGS.md#optional-container-element-read-unlowered); a nested
# def, which has no movable locals (BUGS.md#rebind-slot-missing-module-nested-def);
# a comprehension body, where an outer name is read per iteration and so never
# at a last use (the copy path, with its warning); module level, whose init
# carrier has no movable set. A resumable body (a generator past the
# single-yield peephole, an async def) holds the local as a frame field whose
# pointee storage the frame owns, so it moves the same way.
import asyncio
from typing import Iterator, Optional
from tpy import int32, Own


class Pic:
    def __init__(self, n: int32) -> None:
        self.n = n


class Bag:
    items: list[Pic | None]

    def __init__(self) -> None:
        self.items = []

    def add(self, p: Own[Pic | None]) -> None:
        self.items.append(p)

    @staticmethod
    def put(p: Own[Pic | None], sink: list[Pic | None]) -> None:
        sink.append(p)


def bump_all(patches: list[Pic | None]) -> None:
    for i in range(len(patches)):
        p = patches[i]
        if p is not None:
            p.n += 10


def show(tag: str, patches: list[Pic | None]) -> None:
    for i in range(len(patches)):
        p = patches[i]
        if p is not None:
            print(tag, p.n)
        else:
            print(tag, "none")


def take_own_opt(p: Own[Pic | None], sink: list[Pic | None]) -> None:
    sink.append(p)


# Consumes and returns a scalar, so no handle survives the boundary to mutate
# through; writing through the narrowed param is its own gap
# (BUGS.md#opt-own-param-forward-into-own-opt-slot), and the by-value slot
# drops the payload at return (BUGS.md#opt-own-by-value-param-early-del).
def take_opt_own(p: Own[Pic] | None) -> int32:
    return 0 if p is None else p.n


# The doom shape: declared None, reassigned inside a try, appended per iteration.
def append_try_reassigned(d: dict[bytes, int32]) -> None:
    patches: list[Pic | None] = []
    for name in [b"a", b"b"]:
        patch: Pic | None = None
        try:
            patch = Pic(d[name])
        except KeyError:
            patch = None
        patches.append(patch)  # tpyc: ok
    bump_all(patches)
    show("append_try_reassigned", patches)


# Reassigned only on one arm of an if.
def append_branch_reassigned(c: bool) -> None:
    patches: list[Pic | None] = []
    patch: Pic | None = None
    if c:
        patch = Pic(1)
    patches.append(patch)  # tpyc: ok
    bump_all(patches)
    show("append_branch_reassigned", patches)


# Declared None and never reassigned.
def append_none_declared() -> None:
    patches: list[Pic | None] = []
    patch: Pic | None = None
    patches.append(patch)  # tpyc: ok
    show("append_none_declared", patches)


# Free function and user method slots, and the `Own[record] | None` spelling.
def other_slots(c: bool) -> None:
    sink: list[Pic | None] = []
    patch: Pic | None = None
    if c:
        patch = Pic(5)
    take_own_opt(patch, sink)  # tpyc: ok
    bump_all(sink)
    show("free_own_optional", sink)
    other: Pic | None = None
    if c:
        other = Pic(6)
    print("opt_own_spelling", take_opt_own(other))  # tpyc: ok
    b = Bag()
    third: Pic | None = None
    if c:
        third = Pic(7)
    b.add(third)  # tpyc: ok
    bump_all(b.items)
    show("method_own_optional", b.items)
    fourth: Pic | None = None
    if c:
        fourth = Pic(8)
    Bag.put(fourth, sink)  # tpyc: ok
    bump_all(sink)
    show("static_method", sink)


# A dict stub's element slot, as a statement (the result read-back is its
# own gap, BUGS.md#dict-setdefault-optional-elem-result-ill-formed).
def setdefault_slot(c: bool) -> None:
    d: dict[str, Pic | None] = {}
    patch: Pic | None = None
    if c:
        patch = Pic(3)
    d.setdefault("a", patch)  # tpyc: ok
    stored = d["a"]
    if stored is not None:
        stored.n += 10
    again = d["a"]
    print("setdefault_slot", len(d), 0 if again is None else again.n)


# Generator body on the resumable frame (two yields): the local is a frame
# field pointing at frame-owned storage, moved out at its last use.
def gen(k: int32) -> Iterator[int32]:
    patches: list[Pic | None] = []
    for j in range(k):
        patch: Pic | None = None
        if j != 1:
            patch = Pic(j)
        patches.append(patch)  # tpyc: ok
        yield len(patches)
    bump_all(patches)
    show("generator", patches)
    yield -1


# Async body: the same frame-field local at the element, free-function and
# constructor slots.
async def async_body(c: bool) -> None:
    patches: list[Pic | None] = []
    patch: Pic | None = None
    if c:
        patch = Pic(20)
    patches.append(patch)  # tpyc: ok
    other: Pic | None = None
    if c:
        other = Pic(21)
    take_own_opt(other, patches)  # tpyc: ok
    await asyncio.sleep(0)
    third: Pic | None = None
    if c:
        third = Pic(22)
    b = Bag()
    b.add(third)  # tpyc: ok
    bump_all(patches)
    bump_all(b.items)
    show("async_body", patches)
    show("async_method", b.items)


def main() -> None:
    append_try_reassigned({b"a": 1})
    append_branch_reassigned(True)
    append_branch_reassigned(False)
    append_none_declared()
    other_slots(True)
    other_slots(False)
    setdefault_slot(True)
    for v in gen(3):
        print("generator", v)
    asyncio.run(async_body(True))
    asyncio.run(async_body(False))


main()
