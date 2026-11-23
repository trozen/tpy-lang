# A binding behaves as the TPy function with its signature: a free `-> Node`
# binds the reference it returns, `@readonly` on a method makes its
# receiver and its parameters readonly as on the TPy twin (an unmarked one
# may write its argument), and a `str` argument leaves the caller's views
# of it alone.
from tpy import int32, readonly, Own
from tpy.extern import native
from fixtures import Node


@native
def first_of(n: Node) -> Node: ...


@native
class Bumper:
    def __init__(self) -> None: ...

    @native("bump")
    def bump(self, n: Node) -> None: ...

    @native("peek")
    @readonly
    def peek(self, n: Node) -> int32: ...


@native
def text_len(s: str) -> int32: ...


@native
class Maker:
    @staticmethod
    @native("build")
    def make(v: int32) -> Own[Node]: ...


def via_bumper(b: Bumper, n: Node) -> None:
    # method: its argument is written, so `n` takes `Node&`
    b.bump(n)  # tpyc: ok


def via_peek(b: Bumper, n: Node) -> int32:
    # readonly method: receiver and argument are readonly, so both are const
    return b.peek(n)  # tpyc: ok


def main() -> None:
    n = Node(1)
    # free binding: the result is the argument itself
    r = first_of(n)  # tpyc: ok
    r.v = 4
    print("twin free:", n.v)
    b = Bumper()
    # `r` borrows `n`, and `via_bumper` hands `n` to a body-less writer,
    # which is assumed to move what `n` holds: warned, as it is with the
    # TPy twin `first_of` (BUGS.md#bodyless-writer-borrowed-container-warning)
    via_bumper(b, n)  # tpyc: warning(/Passing borrowed container 'n'/)
    print("twin method:", n.v, via_peek(b, n))
    t = "xabc"
    # str argument: the view `v` stays a view
    v = t[1:]
    k = text_len(t)  # tpyc: ok
    print("twin str arg:", v, k)
    # a renamed static of a native class: `Maker::build`
    m = Maker.make(7)  # tpyc: ok
    print("twin native static:", m.v)
