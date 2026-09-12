# Dict and set container-literal locals routed through THIR (--thir-codegen,
# byte-identical to the AST path): a dict literal local feeds subscript reads,
# pop with default, len, and key iteration; a set literal local feeds len and
# iteration. Local-only value semantics (scalar keys/values), no aliasing.
# Iteration results are SUMMED so the assertions stay order-independent
# (CPython set order is hash-based; TPy's ordered_set is insertion-ordered).
from tpy import int32


def dict_ops() -> int32:
    d = {1: 100, 2: 200, 3: 300}
    total = d[1]
    total = total + d.pop(2)
    total = total + d.pop(9, 4)
    for k in d:
        total = total + k
    return total + len(d)


def empty_dict(k: int32, v: int32) -> int32:
    e: dict[int32, int32] = {}
    print(len(e))
    return e.pop(k, v)


def set_ops() -> int32:
    s = {5, 6, 7}
    total = len(s)
    for v in s:
        total = total + v
    return total


def main() -> None:
    print(dict_ops())
    print(empty_dict(1, 42))
    print(set_ops())


main()
