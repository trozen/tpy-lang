# Return-tier sibling: `raise E(rec)` with a `Record | None` ctor param lowers
# through make_unexpected via the shared loop's optional-ptr arm; copy intended.
from tpy import error_return, ReturnException


class Node:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


class Failed(Exception, ReturnException):
    node: Node | None

    def __init__(self, n: Node | None) -> None:
        super().__init__()
        self.node = n


@error_return(Failed)
def run(ok: bool) -> int:
    if ok:
        return 1
    raise Failed(Node(9))


def main() -> None:
    try:
        print(run(True))
    except Failed:
        print("unexpected")
    try:
        print(run(False))
    except Failed as e:
        if e.node is not None:
            print(e.node.v)


main()
