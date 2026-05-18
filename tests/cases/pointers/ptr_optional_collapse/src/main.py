# Ptr[T] | None collapses to Ptr[T] at type-construction time, since T* is
# already nullable. The redundant spelling is warned about (upstream #17) but
# still compiles to the same lowering. The T | None <-> Ptr[T] boundary
# coerces in both directions because both lower to T* at C++ level.
from tpy import Ptr, readonly


class Node:
    val: int
    def __init__(self, v: int) -> None:
        self.val = v


# Demonstrative: `Ptr[T] | None` is redundant; the warning fires at the
# spelling site (param + return); the type collapses to `Ptr[T]`.
def passthrough(
    n: Ptr[Node] | None,            # tpyc: warning(/`Ptr\[Node\] \| None` is redundant/)
) -> Ptr[Node] | None:              # tpyc: warning(/`Ptr\[Node\] \| None` is redundant/)
    return n


# Readonly inner variant also collapses + warns.
def passthrough_ro(
    n: Ptr[readonly[Node]] | None,  # tpyc: warning(/`Ptr\[readonly\[Node\]\] \| None` is redundant/)
) -> Ptr[readonly[Node]] | None:    # tpyc: warning(/`Ptr\[readonly\[Node\]\] \| None` is redundant/)
    return n


# T | None nullable-return idiom feeding a pointer storage slot.
def first(items: list[Node]) -> Node | None:
    if len(items) > 0:
        return items[0]
    return None


# Body returns a Ptr[T] value into a `T | None` return slot.
def find(items: list[Node], target: int) -> Node | None:
    for it in items:
        if it.val == target:
            p: Ptr[Node] = it
            return p
    return None


# Ptr[T] flowing into a `T | None` call argument.
def consume(n: Node | None) -> int:
    if n is not None:
        return n.val
    return -1


# Mutable Ptr[T] -> readonly[T] | None (inner-narrowing direction is safe).
def consume_ro(n: readonly[Node] | None) -> int:
    if n is not None:
        return n.val
    return -1


# Ptr[T] flowing into a `T | None` tuple slot.
def first_pair(items: list[Node]) -> tuple[Node | None, int]:
    if len(items) > 0:
        p: Ptr[Node] = items[0]
        return (p, items[0].val)
    return (None, 0)


# Storage-form Optional source through codegen lift (`optional_to_ptr`).
class Holder:
    opt: Node | None
    def __init__(self, v: Node) -> None:
        self.opt = v


def take_ptr_node(p: Ptr[Node]) -> int:
    if p is not None:
        return p.val
    return -1


def test_collapse_passthrough() -> None:
    items: list[Node] = [Node(1)]
    print(passthrough(None) is None)
    p1: Ptr[Node] = items[0]
    r = passthrough(p1)
    if r is not None:
        print(r.val)


def test_optional_return_into_ptr_local() -> None:
    items: list[Node] = [Node(1)]
    p: Ptr[Node] = first(items)
    if p is not None:
        print(p.val)


def test_ptr_value_into_optional_return() -> None:
    items: list[Node] = [Node(1), Node(2), Node(3)]
    found = find(items, 2)
    if found is not None:
        print(found.val)
    print(find(items, 99) is None)


def test_readonly_variant() -> None:
    items: list[Node] = [Node(3)]
    cp: Ptr[readonly[Node]] = items[0]
    rc = passthrough_ro(cp)
    if rc is not None:
        print(rc.val)


def test_ptr_into_optional_call_arg() -> None:
    items: list[Node] = [Node(1)]
    p: Ptr[Node] = items[0]
    print(consume(p))


def test_ptr_into_optional_tuple_slot() -> None:
    items: list[Node] = [Node(1)]
    pair = first_pair(items)
    print(pair[1])


def test_mutable_to_readonly_widening() -> None:
    items: list[Node] = [Node(1)]
    p: Ptr[Node] = items[0]
    print(consume_ro(p))


def test_storage_form_optional_lifts_to_ptr() -> None:
    h = Holder(Node(2))
    print(take_ptr_node(h.opt))


def main() -> None:
    test_collapse_passthrough()
    test_optional_return_into_ptr_local()
    test_ptr_value_into_optional_return()
    test_readonly_variant()
    test_ptr_into_optional_call_arg()
    test_ptr_into_optional_tuple_slot()
    test_mutable_to_readonly_widening()
    test_storage_form_optional_lifts_to_ptr()


main()
