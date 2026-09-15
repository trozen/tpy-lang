# Truthiness of a STORAGE-form Optional whose inner record carries no
# __bool__/__len__: Python's answer is "is not None", which is exactly what the
# std::optional engagement test renders, so the bare read admits. Covers the
# record-field, chained-field and container-element sources, plus a value-repr
# Optional field read off a SUBSCRIPT receiver. The dunder-carrying inner is
# bool/bool_unnarrowed_optional's; the container inner keeps rejecting
# (error_truthy_optional_container_field).
from tpy import int32


class Node:
    def __init__(self, v: int32) -> None:
        self.v = v


class Holder:
    node: Node | None

    def __init__(self, node: Node | None) -> None:
        self.node = node


class Outer:
    inner: Holder

    def __init__(self, inner: Holder) -> None:
        self.inner = inner


class Scalar:
    opt: int32 | None

    def __init__(self, opt: int32 | None) -> None:
        self.opt = opt


# free function: the record-Optional field is the plain engagement test.
def field_test(h: Holder) -> int32:
    if h.node:  # tpyc: ok
        return 1
    return 0


# free function: a `while` head over the same source.
def while_test(h: Holder) -> int32:
    n = 0
    while h.node:  # tpyc: ok
        n += 1
        h.node = None
    return n


# free function: the one-link chain flavor.
def chain_test(o: Outer) -> int32:
    if o.inner.node:  # tpyc: ok
        return 1
    return 0


# free function: the container-ELEMENT source.
def elem_test(xs: list[Node | None]) -> int32:
    if xs[0]:  # tpyc: ok
        return 1
    return 0


# free function: a value-repr Optional field read off an element receiver.
def subscript_recv_test(hs: list[Scalar]) -> int32:
    if hs[0].opt:  # tpyc: ok
        return 1
    return 0


class Reader:
    slot: Node | None

    def __init__(self, slot: Node | None) -> None:
        self.slot = slot

    # method: `self` as the field receiver.
    def has(self) -> int32:
        if self.slot:  # tpyc: ok
            return 1
        return 0


def main() -> None:
    print("field", field_test(Holder(None)), field_test(Holder(Node(3))))
    print("while", while_test(Holder(None)), while_test(Holder(Node(3))))
    print("chain", chain_test(Outer(Holder(None))),
          chain_test(Outer(Holder(Node(3)))))
    print("elem", elem_test([None]), elem_test([Node(3)]))
    print("subrecv", subscript_recv_test([Scalar(None)]),
          subscript_recv_test([Scalar(0)]), subscript_recv_test([Scalar(5)]))
    print("method", Reader(None).has(), Reader(Node(3)).has())


main()
