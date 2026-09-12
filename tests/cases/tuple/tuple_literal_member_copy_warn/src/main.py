# A value-tuple's reference (pointer-repr) member copied from a whole-tuple
# lvalue source into a list/dict/append owned slot warns per member (a fresh
# rvalue member and an explicit copy() are exempt). Output is read-only, so it
# matches CPython; the warning is the acknowledgment of the silent copy.
from tpy import int32, copy


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def list_literal(items: list[tuple[int32, P]]) -> None:
    # annotated list[...] -> std::vector (storage form); a bare single-element
    # literal would infer a fixed Array and hit a separate borrow-form codegen
    # gap (see BUGS.md).
    xs: list[tuple[int32, P]] = [items[0]]  # tpyc: warning(/copies P into owned storage/)
    print(len(xs))


def dict_value(items: list[tuple[int32, P]]) -> None:
    d: dict[int32, tuple[int32, P]] = {0: items[0]}  # tpyc: warning(/copies P into owned storage/)
    print(len(d))


def via_append(items: list[tuple[int32, P]]) -> None:
    out: list[tuple[int32, P]] = []
    out.append(items[0])  # tpyc: warning(/copies P into owned storage/)
    print(len(out))


def exempt_fresh(n: int32) -> None:
    xs: list[tuple[int32, P]] = [(1, P(9))]  # tpyc: ok
    print(len(xs))


def exempt_copy(p: P) -> None:
    xs: list[tuple[int32, P]] = [(1, copy(p))]  # tpyc: ok
    print(len(xs))


def main() -> None:
    src: list[tuple[int32, P]] = [(1, P(5))]
    list_literal(src)
    dict_value(src)
    via_append(src)
    exempt_fresh(0)
    exempt_copy(P(8))


main()
