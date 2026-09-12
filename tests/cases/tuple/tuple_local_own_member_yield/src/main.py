# The escape the dangle diagnostic points to: annotate the tuple element
# Own[Box]. The fresh Box then moves into the storage-form yield slot, so a
# bare-name yield of the owning local is valid (no borrow taken). Also confirms
# the owns-fresh flag does NOT fire when the element type is Own. One section per
# position; a two-yield generator is not "simple", so those sections take the
# resumable frame, where a slot dead after its yield MOVES out and a slot still
# live copies (warned). Copy-vs-move is unobservable here by construction: the
# consumer owns the handed-out Box either way -- except in the warned sections,
# where the generator keeps reading its slot after the resume, so the copy is
# what makes the read valid.
from typing import Iterator
from tpy import Int32, Own, take_ptr


class Box:
    val: Int32
    # A heap member, so a slot moved out from under a live borrow is observable
    # (the borrowed read sees the stolen buffer) rather than silently fine.
    items: list[Int32]

    def __init__(self, v: Int32) -> None:
        self.val = v
        self.items = [v]


# Reads the heap member through a borrow, so a moved-from Box shows up as an
# empty list rather than an intact `val`.
def first_item(b: Box) -> Int32:
    return b.items[0]


def mk(v: Int32) -> Own[tuple[Int32, Own[Box]]]:
    return (v, Box(v * 10))


def gen(n: Int32) -> Iterator[tuple[Int32, Own[Box]]]:
    i = Int32(0)
    while i < n:
        t = (i, Box(i * 10))
        yield t  # tpyc: ok
        i += 1


# Free function, two yields -- the resumable frame; each slot is dead after its
# own yield, so both move out.
def gen_twice(n: Int32) -> Iterator[tuple[Int32, Own[Box]]]:
    i = Int32(0)
    while i < n:
        t = (i, Box(i * 10))
        yield t  # tpyc: ok
        u = (i + 100, Box(i))
        yield u  # tpyc: ok
        i += 1


# The owning-CALL init position.
def gen_call_init(n: Int32) -> Iterator[tuple[Int32, Own[Box]]]:
    i = Int32(0)
    while i < n:
        # An owning CALL init keeps the per-element Own markers a literal init
        # drops, and binds the same storage tuple.
        t = mk(i)
        yield t  # tpyc: ok
        v = mk(i + 100)
        yield v  # tpyc: ok
        i += 1


# The owning-CALL init at a SINGLE yield -- the peephole ladder's half of the
# same arm (the two-yield sibling above takes the resumable frame).
def gen_call_init_once(n: Int32) -> Iterator[tuple[Int32, Own[Box]]]:
    i = Int32(0)
    while i < n:
        t = mk(i)
        yield t  # tpyc: ok
        i += 1


# The owning-CALL init under a HIDDEN borrow: `saved` borrows into the slot and
# is read after the resume, so the last-use mark is retracted and the slot
# copies out. Moving it would hand the consumer the slot's buffer and leave
# `saved` reading a moved-from Box.
def gen_call_init_borrowed(n: Int32) -> Iterator[tuple[Int32, Own[Box]]]:
    i = Int32(0)
    while i < n:
        t = mk(i)
        saved = take_ptr(t[1])
        yield t  # tpyc: warning(/copies tuple.* into owned storage/)
        print("borrowed", saved.items[0])
        u = mk(i + 100)
        yield u  # tpyc: ok
        i += 1


# The same hidden borrow, but DEAD before the yield: `saved`'s last read is the
# line above the suspension, so moving the slot out would be legal. The loan
# tracker holds a loan for the borrower's whole scope rather than to its last
# read, so the yield still copies and warns -- pinned as the conservative
# answer, not as the desired one.
def gen_call_init_borrow_dead(n: Int32) -> Iterator[tuple[Int32, Own[Box]]]:
    i = Int32(0)
    while i < n:
        t = mk(i)
        saved = take_ptr(t[1])
        print("dead-borrow", saved.items[0])
        yield t  # tpyc: warning(/copies tuple.* into owned storage/)
        u = mk(i + 100)
        yield u  # tpyc: ok
        i += 1


# The tuple is bound BEFORE the loop, so the slot is live at every iteration:
# each pass copies out (one warning at the yield site) instead of moving, and
# the slot still holds its Box once the loop is done.
def gen_preloop(n: Int32) -> Iterator[tuple[Int32, Own[Box]]]:
    t = mk(7)
    for _ in range(n):
        yield t  # tpyc: warning(/copies tuple.* into owned storage/)
    print("preloop-kept", first_item(t[1]))


# The read-after-yield position.
def gen_live(n: Int32) -> Iterator[tuple[Int32, Own[Box]]]:
    i = Int32(0)
    while i < n:
        t = (i, Box(i * 10))
        # `t` is read after the yield, so the slot is still live: the tuple
        # copies out instead of moving, which is the leg sema warns on. The
        # consumer deliberately does not mutate the yielded Box here -- that
        # copy IS a CPython divergence, tracked at
        # BUGS.md#tuple-yield-copy-warning-omits-aliasing.
        yield t  # tpyc: warning(/copies tuple.* into owned storage/)
        print("live-gen", t[1].val)
        u = (i, Box(i))
        yield u  # tpyc: ok
        i += 1


class Src:
    base: Int32

    def __init__(self, base: Int32) -> None:
        self.base = base

    # Method position on the frame: the slot reads through `__self`.
    def pairs(self, n: Int32) -> Iterator[tuple[Int32, Own[Box]]]:
        i = Int32(0)
        while i < n:
            t = (self.base + i, Box(i * 10))
            yield t  # tpyc: ok
            u = (self.base - i, Box(i))
            yield u  # tpyc: ok
            i += 1


def main() -> None:
    for pair in gen(3):
        print("free", pair[0], pair[1].val)

    for pair in gen_twice(2):
        # The consumer owns the handed-out Box: the mutation is visible through
        # the tuple it arrived in, and the generator's next bind cannot clobber it.
        pair[1].val += 1
        print("twice", pair[0], pair[1].val)

    for pair in gen_call_init(2):
        print("call", pair[0], pair[1].val)

    for pair in gen_call_init_once(2):
        # The move leg: the consumer owns the handed-out Box, so its mutation
        # is visible through the tuple it arrived in.
        pair[1].val += 1
        print("call-once", pair[0], pair[1].val)

    for pair in gen_call_init_borrowed(2):
        # No mutation here: the warned yield copies, so a mutation would show
        # the aliasing divergence tracked at
        # BUGS.md#tuple-yield-copy-warning-omits-aliasing rather than this
        # section's subject (the generator's own read after the resume).
        print("borrowed-consumer", pair[0], pair[1].val)

    for pair in gen_call_init_borrow_dead(2):
        print("dead-consumer", pair[0], pair[1].val)

    for pair in gen_preloop(2):
        print("preloop", pair[0], pair[1].val)

    for pair in gen_live(2):
        print("live", pair[0], pair[1].val)

    s = Src(100)
    for pair in s.pairs(2):
        print("method", pair[0], pair[1].val)


main()
