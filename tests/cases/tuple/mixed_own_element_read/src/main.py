# A per-element-Own tuple renders MIXED (`std::tuple<A, B*>`), so subscripting
# its plain ref element must use `->` while its Own element stays `.`.
from tpy import Int32, Own, copy


class Box:
    val: Int32

    def __init__(self, val: Int32) -> None:
        self.val = val

    def bump(self) -> Int32:
        self.val = self.val + 1
        return self.val


def make_mixed(b: Box) -> tuple[Own[Box], Box]:
    return (Box(1), b)


def make_owned() -> Own[tuple[Box, Box]]:
    return (Box(3), Box(4))


def take(t: tuple[Box, Box]) -> Int32:
    return t[0].val + t[1].val


def read_borrow_elem(b: Box) -> Int32:
    p = make_mixed(b)
    return p[1].val


def read_own_elem(b: Box) -> Int32:
    p = make_mixed(b)
    return p[0].val


def write_borrow_elem(b: Box) -> Int32:
    p = make_mixed(b)
    p[1].val = 99
    # Observed on the ORIGINAL: element 1 aliases `b`, it is not a copy.
    return b.val


def read_direct(b: Box) -> Int32:
    # No local: the call result is subscripted in place, the other shape whose
    # whole-tuple storage verdict used to veto the per-element answer.
    return make_mixed(b)[1].val


def read_param(p: tuple[Own[Box], Box]) -> Int32:
    # A param is not storage-form to begin with, so this shape always worked --
    # it is the inverse guarding against the fix over-reaching.
    return p[1].val


def unpack_mixed(b: Box) -> Int32:
    owned, borrowed = make_mixed(b)
    return owned.val + borrowed.val


def method_on_borrow_elem(b: Box) -> Int32:
    # A method receiver composes the arrow decision separately from the
    # field-access site, over the same predicate.
    p = make_mixed(b)
    p[1].bump()
    return b.val


def pass_whole(b: Box) -> Int32:
    p = make_mixed(b)
    n = take(p)
    # The whole-tuple lift must alias too, not snapshot: mutate after it and
    # read the original.
    p[1].val = 50
    return n + b.val


def wholly_owned_still_dots() -> Int32:
    p = make_owned()
    return p[1].val


# The storage sinks below all run the mixed tuple through tuple_to_storage,
# which materializes the ref element BY VALUE -- so they must keep reading `.`,
# even though the tuple type still carries the `Own`. Reading the borrow form
# off the TYPE instead of the SOURCE breaks exactly these.
#
# Every one of them COPIES the borrowed element -- a container owns its
# elements, so element 1 behaves as a standalone `Box` would there: copied,
# with the same warning the scalar store gives, silenced by copy(). All four
# agree on that because the inferred element type takes the owned storage form.
#
# The copy is an ACKNOWLEDGED divergence from CPython, which aliases -- the
# warnings are the acknowledgement. Observing it needs a mutate-after-store,
# which by construction cannot agree with CPython's output, so it lives in the
# sibling `mixed_own_tuple_store_copies` (no_cpython). These read only, so this
# case stays cpy-visible; the annotations are what pin the copy here. Do NOT
# add no_cpython.txt to this case.
def in_list(b: Box) -> Int32:
    xs = [make_mixed(b)]  # tpyc: warning(/copies Box into owned storage/)
    return xs[0][1].val


def in_dict(b: Box) -> Int32:
    d = {1: make_mixed(b)}  # tpyc: warning(/copies Box into owned storage/)
    return d[1][1].val


def in_nested_tuple(b: Box) -> Int32:
    # No warning here: the member is itself a value tuple, so its borrowed
    # element is a level deeper than the per-member check looks (the
    # nested-depth limit in BUGS.md). It copies all the same.
    q = (make_mixed(b), 1)
    return q[0][1].val


def as_loop_var(b: Box) -> Int32:
    xs = [make_mixed(b)]  # tpyc: warning(/copies Box into owned storage/)
    n = 0
    for t in xs:
        n = n + t[1].val
    return n


def in_list_copy_ack(b: Box) -> Int32:
    # The acknowledgement silences the warning; copy() of a mixed tuple yields
    # the fully-owned storage form, so this is the same copy, made explicit --
    # and CPython's copy() deep-copies, so this shape agrees with CPython.
    xs = [copy(make_mixed(b))]  # tpyc: ok
    xs[0][1].val = 41
    return b.val


def via_ternary(b: Box, c: Box, flag: bool) -> Int32:
    # Both arms are the borrow render, so the ternary is too -- the sibling
    # composition rule `is_storage_form_source` already spells.
    p = make_mixed(b) if flag else make_mixed(c)
    return p[1].val


def rebound_in_branch(b: Box, c: Box, flag: bool) -> Int32:
    # Reads the mixed local inside both arms, so the per-branch snapshot and
    # restore of the local sets has to carry the binding across.
    p = make_mixed(b)
    if flag:
        n = p[1].val
    else:
        n = p[1].val + make_mixed(c)[1].val
    return n


def main() -> None:
    print(read_borrow_elem(Box(7)))
    print(read_own_elem(Box(7)))
    print(write_borrow_elem(Box(7)))
    print(read_direct(Box(7)))
    print(read_param((Box(5), Box(6))))
    print(unpack_mixed(Box(7)))
    print(method_on_borrow_elem(Box(7)))
    print(pass_whole(Box(7)))
    print(wholly_owned_still_dots())
    print(in_list(Box(2)), in_dict(Box(2)))
    print(in_nested_tuple(Box(2)), as_loop_var(Box(2)))
    print(in_list_copy_ack(Box(2)))
    print(via_ternary(Box(2), Box(3), True))
    print(rebound_in_branch(Box(2), Box(3), False))


main()
