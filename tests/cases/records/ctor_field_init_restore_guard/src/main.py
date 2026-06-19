# Restore guard: A's all-MIL ctor (no body, so no setup_body_scope reset after
# it) seeds pointer_locals for its optional-ptr param `n`. Without the scoped
# restore, that classification leaks into B's all-MIL ctor and would deref B's
# plain int `n` (emit `x(*n)`). Pins x(n) / the correct deref boundary.
class Node:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


class A:
    got: int

    def __init__(self, n: Node | None) -> None:
        self.got = n.v if n is not None else -1


class B:
    x: int

    def __init__(self, n: int) -> None:
        self.x = n


def main() -> None:
    print(A(Node(8)).got)
    print(B(3).x)


main()
