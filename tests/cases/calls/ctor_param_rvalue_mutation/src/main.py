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
    def __init__(self, n: Node) -> None:
        # Mutating use of the param -- without this, the param would
        # const-infer to `const Node&` and the rvalue-binding question
        # wouldn't arise. With this, the param lowers as `Node&` and
        # the call site must synthesize a temp.
        take_mut(n)


def main() -> None:
    _ = Sink(Node(1))
    print("ok")


main()
