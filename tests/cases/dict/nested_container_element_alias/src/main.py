# Nested-container element reads (`g["a"]` off dict[str, list[int32]]) in
# value positions. Reference semantics are the point: the element read is a
# borrow into the dict's own storage, so a local bound from it and a callee
# that mutates its param must both be visible through the dict afterwards.
# The dicts are built by assignment, not by a nested literal -- an annotated
# dict-literal-of-list-literals is rejected by sema (see BUGS.md).
from tpy import int32


def push(xs: list[int32], v: int32) -> None:
    xs.append(v)


def read_elements(g: dict[str, list[int32]], m: list[list[int32]]) -> int32:
    # The value-position element reads themselves -- kept in their own body so
    # this half actually ROUTES through the element gate arm (main() below
    # carries positions that still reject, which would fall the whole body
    # back and leave the arm unexercised at exec).
    print(len(g["a"]))
    print(g["a"])
    row = g["a"]
    row.append(99)
    g["a"].append(4)
    return len(g["a"]) + len(m[1])


def main() -> None:
    a: list[int32] = [1, 2]
    b: list[int32] = [3]
    g: dict[str, list[int32]] = {}
    g["a"] = a
    g["b"] = b

    # A bound local aliases the dict's storage -- the mutation is observable
    # through the dict, not just through the local.
    r0: list[int32] = [1]
    r1: list[int32] = [2, 3]
    m: list[list[int32]] = [r0, r1]
    print(read_elements(g, m))

    # Same through a callee taking the element directly.
    push(g["b"], 7)
    print(g["b"])

    # Read positions that never had a per-sink row of their own.
    total = 0
    for v in g["a"]:
        total += v
    print(total)

    m[1].append(4)
    print(len(m[1]))
    print(m[1])


main()
