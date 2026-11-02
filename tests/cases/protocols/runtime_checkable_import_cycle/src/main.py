# A decorated Root and the undecorated Mid / Child that inherit it sit in
# two modules importing each other; Child is runtime-checkable through the
# cycle peer's parent, so its isinstance does not warn; Lone, a cycle member
# with no decorated ancestor, still warns.
from typing import Protocol
from a import Child, LoneChild
from b import Lone


class Walker(Protocol):
    def walk(self) -> int: ...


class C:
    def __init__(self) -> None:
        pass

    def f(self) -> str:
        return "yes"


class W:
    def __init__(self) -> None:
        pass

    def walk(self) -> int:
        return 4


def check(x: Child | Walker) -> str:
    if isinstance(x, Child):  # tpyc: ok
        return x.f()
    return "walks"


# Never called: CPython raises TypeError at this isinstance, since Lone is
# not runtime-checkable; sema still analyzes the body and warns.
def lone(x: Lone | Walker) -> str:
    if isinstance(x, Lone):  # tpyc: warning(/TPy-only/)
        return x.g()
    return "walks"


# Never called, as lone(): the undecorated cross-module ancestor chain warns.
def lone_child(x: LoneChild | Walker) -> str:
    if isinstance(x, LoneChild):  # tpyc: warning(/TPy-only/)
        return x.g()
    return "walks"


def main() -> None:
    c = C()
    w = W()
    print("cycle", check(c), check(w))


main()
