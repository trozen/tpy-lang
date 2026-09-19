# One @property read at every POSITION the scope matrix had no case for, now
# that the read is one node kind everywhere: constructor, module-level
# statement, comprehension, closure, context-manager body, try/finally,
# @error_return body, match subject, a field store, and the EXPRESSION
# positions the collapse newly admits -- a free-function argument, a method
# argument, a return, a tuple literal and a container insert.
# The getter returns a borrow of the stored list, so every section that can
# MUTATES through what it read and the owner observes it -- a silent copy would
# show in output.txt. Each section prints its own name so a divergence names the
# cell. TWO sections cannot: `container_insert` and `tuple_literal` are ELEMENT
# slots, which take a COPY of what the getter lent. That copy is DECLARED for a
# record element (the `copies Rec into owned storage` warning pinned below) and
# it is a divergence from CPython, which aliases -- so those two observe the
# element BEFORE mutating the owner rather than after, since observing after
# would make output.txt disagree with CPython by design. The copy is the
# spelled-METHOD twin's render too, which is why the getter takes it.
# The REJECTING positions are pinned elsewhere: the augmented assignment by
# records/error_property_augassign, the deep chain in a frame by
# generators/error_gen_foreach_property_chain, and an aggregate element over a
# getter that lends the receiver's STORAGE (`(b.rec, 1)`) by
# records/error_property_elem_lends_storage -- which is why `tuple_literal`
# below reads `b.rec.x` and not `b.rec`.
# `return_pos` pins a pre-existing double evaluation: `borrow_out(b).x += 1`
# renders the call twice (read and write-back), so an impure getter behind it
# would run twice -- BUGS.md#augassign-call-receiver-double-eval, whose shape
# is any augmented assignment through a CALL receiver.
from typing import Iterator

from tpy import ReturnException, StrView, error_return, int32


class BagErr(Exception, ReturnException):
    pass


class Rec:
    x: int32

    def __init__(self) -> None:
        self.x = 1


class Guard:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __enter__(self) -> int32:
        self.n += 1
        return self.n

    def __exit__(self, kind, value, tb) -> None:
        pass


class Bag:
    _items: list[int32]
    _rec: Rec
    _guard: Guard
    tag: str

    def __init__(self) -> None:
        self._items = [1, 2]
        self._rec = Rec()
        self._guard = Guard()
        self.tag = "a"

    @property
    def items(self) -> list[int32]:
        return self._items

    @property
    def rec(self) -> Rec:
        return self._rec

    @property
    def kind(self) -> StrView:
        return self.tag

    @property
    def guard(self) -> Guard:
        return self._guard


class Holder:
    n: int32
    first: int32

    # constructor: the read feeds a field store, and MUTATES through the
    # getter first so the owner observes the write (a copy would not show)
    def __init__(self, b: Bag) -> None:
        b.items.append(3)  # tpyc: ok
        self.n = len(b.items)  # tpyc: ok
        self.first = b.items[0]  # tpyc: ok


def comprehension(b: Bag) -> None:
    doubled = [x * 2 for x in b.items]  # tpyc: ok
    b.items.append(9)
    print("comprehension:", len(doubled), len(b._items))


def closure(b: Bag) -> None:
    def add_one() -> None:
        b.items.append(1)  # tpyc: ok

    add_one()
    print("closure:", len(b._items))


def context_manager(b: Bag) -> None:
    with Guard() as _g:
        b.items.append(7)  # tpyc: ok
    print("context_manager:", len(b._items), b._items[2])


# the getter READ as the with-manager itself: it hands back a reference into
# the receiver's storage, so the region must BORROW it -- the owner observes
# the mutation __enter__ made, which a copied manager would lose
def manager_is_getter(b: Bag) -> None:
    with b.guard as g:  # tpyc: ok
        print("manager_is_getter:", g)
    print("manager_is_getter owner:", b._guard.n)


# the same manager inside a FRAME, whose `with` region spans a suspension:
# the frame borrows the getter's result exactly as the sync region does, so
# the owner still sees the single `__enter__` the region ran
def frame_manager(b: Bag) -> Iterator[int32]:
    with b.guard as g:  # tpyc: ok
        yield g
        yield g


def try_finally(b: Bag) -> None:
    try:
        b.items.append(8)  # tpyc: ok
    finally:
        b.items.append(8)  # tpyc: ok
    print("try_finally:", len(b._items))


@error_return(BagErr)
def error_return_body(b: Bag) -> int32:
    b.items.append(5)  # tpyc: ok
    return len(b.items)  # tpyc: ok


# free-function argument: the RECORD getter's borrow reaches the callee, which
# mutates through it, and the owner observes the write
def sink(r: Rec) -> None:
    r.x += 1


def free_fn_arg(b: Bag) -> None:
    sink(b.rec)  # tpyc: ok
    print("free_fn_arg:", b._rec.x)


class Sinker:
    # method argument: the same boundary one receiver over
    def take(self, r: Rec) -> None:
        r.x += 1


def method_arg(b: Bag) -> None:
    Sinker().take(b.rec)  # tpyc: ok
    print("method_arg:", b._rec.x)


# return: the getter's borrow is handed on, and the caller mutates through it
def borrow_out(b: Bag) -> Rec:
    return b.rec  # tpyc: ok


def return_pos(b: Bag) -> None:
    borrow_out(b).x += 1
    print("return_pos:", b._rec.x)


# container insert: the getter RESULT itself at an element slot -- a WARNED copy
# of the record it lent, where CPython aliases (see the header), plus the scalar
# read one hop down, which is a copy in both languages
def container_insert(b: Bag) -> None:
    acc: list[Rec] = []
    acc.append(b.rec)  # tpyc: warning(/copies Rec into owned storage/)
    scalars: list[int32] = []
    scalars.append(b.rec.x)  # tpyc: ok
    print("container_insert:", acc[0].x, scalars[0])
    b.rec.x = 99
    print("container_insert owner:", b._rec.x)


# tuple literal: the same copy boundary, in a value tuple
def tuple_literal(b: Bag) -> None:
    t = (b.rec.x, len(b.items))  # tpyc: ok
    b.rec.x = 77
    print("tuple_literal:", t[0], t[1], b._rec.x)


def match_subject(b: Bag) -> None:
    # the arm mutates through the CONTAINER getter so the owner observes it;
    # the subject read itself is the view-returning getter
    match b.kind:  # tpyc: ok
        case "a":
            b.items.append(6)  # tpyc: ok
            print("match_subject: a", len(b._items), b._items[2])
        case _:
            print("match_subject: other")


# module-level statement: top-level codegen binds globals through pointer slots,
# so the read renders in a different variable model from a function body's
global_bag = Bag()
global_bag.items.append(4)  # tpyc: ok
global_len = len(global_bag.items)  # tpyc: ok


def main() -> None:
    print("module_level:", global_len, len(global_bag._items))
    cb = Bag()
    print("constructor:", Holder(cb).n, Holder(cb).first, len(cb._items))
    comprehension(Bag())
    closure(Bag())
    context_manager(Bag())
    manager_is_getter(Bag())
    fb = Bag()
    for v in frame_manager(fb):
        print("frame_manager:", v)
    print("frame_manager owner:", fb._guard.n)
    try_finally(Bag())
    eb = Bag()
    try:
        print("error_return:", error_return_body(eb), len(eb._items))
    except BagErr:
        print("error_return: raised")
    match_subject(Bag())
    free_fn_arg(Bag())
    method_arg(Bag())
    return_pos(Bag())
    container_insert(Bag())
    tuple_literal(Bag())


main()
