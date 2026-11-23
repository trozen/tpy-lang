# A result borrowing a tuple parameter's OWNED element points into the
# tuple temporary the caller built, which dies with the statement: warned
# for the concrete function and its generic twin alike.
from tpy import int32, Own


class Node:
    def __init__(self, v: int32) -> None:
        self.v = v


def first_o(t: tuple[Own[Node], int32]) -> Node:  # tpyc: warning(/owned tuple param 't' is never consumed/)
    return t[0]


def first_go[T](t: tuple[Own[T], int32]) -> T:  # tpyc: warning(/owned tuple param 't' is never consumed/)
    return t[0]


def main() -> None:
    # the results are never read: they would dangle
    n = first_o((Node(1), 2))  # tpyc: warning(/Result borrows from temporary argument 't'/)
    m = first_go((Node(2), 2))  # tpyc: warning(/Result borrows from temporary argument 't'/)
    # a number result copies the element out
    k = first_go((5, 2))  # tpyc: ok
    print("own element:", k)
