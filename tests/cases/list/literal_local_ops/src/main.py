# Container-literal locals routed through THIR (--thir-codegen, byte-identical
# to the AST path): a mutated list literal resolves to std::vector (mutation
# keeps it a list), a read-only one demotes to std::array; both then feed the
# already-routed receiver shapes -- method calls, subscript reads, len(),
# iteration -- inside the same function. Empty annotated literal covered too.
# Semantics are local-only (value copies of scalars), no aliasing at play.
from tpy import int32


def grown() -> int32:
    xs = [3, 1]
    xs.append(9)
    xs.insert(0, 7)
    xs.sort()
    total = 0
    for x in xs:
        total = total + x
    return total


def fixed() -> int32:
    ys = [10, 20, 30]
    total = ys[0]
    total = total + ys[2]
    for i in range(len(ys)):
        total = total + ys[i]
    return total


def from_empty(n: int32) -> int32:
    zs: list[int32] = []
    zs.append(n)
    zs.append(n + 1)
    # len is read before the pop mutates: a `len(zs) + zs.pop()` binop would be
    # C++-argument-order-dependent (CPython evaluates left-to-right).
    count = len(zs)
    return count + zs.pop()


def main() -> None:
    print(grown())
    print(fixed())
    print(from_empty(5))


main()
