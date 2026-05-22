# Constructor accepts an rvalue arg whose param is mutated inside the
# body (lowers as `T&` in C++). Codegen synthesizes a named temp at the
# call site so the rvalue binds to the reference, matching the
# free-function and method-call paths. Without the temp synthesis, C++
# rejected with "cannot bind non-const lvalue reference to rvalue".
from tpy import Ptr


class Node:
    value: int

    def __init__(self, v: int) -> None:
        self.value = v


def take_mut(p: Ptr[Node]) -> None:
    p.value = 99


class Sink:
    captured: int

    def __init__(self, n: Node) -> None:
        # Mutating use of the param. Two effects:
        # (1) the param const-infers to `Node&` (was `const Node&`), so the
        #     call site must synthesize a temp to bind the rvalue Node(1).
        # (2) `self.captured = n.value` AFTER the side-effecting call must
        #     read the post-mutation value (99) -- the MIL hoist must stop
        #     at `take_mut(n)`, not pull the field assign ahead of it.
        take_mut(n)
        self.captured = n.value


def main() -> None:
    s = Sink(Node(1))
    print(s.captured)


main()
