# A result may point into a `Ptr` operand or a `String` passed by
# reference: an undeclared free or method binding's result lends from
# them, and `result=` "borrow" may name them.
from tpy import int32, Ptr, String, StrView, take_ptr
from tpy.extern import native
from fixtures import Node


@native
def through(p: Ptr[Node]) -> Node: ...


@native(result={"s": "borrow"})
def view_of(s: String) -> StrView: ...


@native
class Via:
    def __init__(self) -> None: ...

    def through(self, p: Ptr[Node]) -> Node: ...

    def view_of(self, s: String) -> StrView: ...


def main() -> None:
    n = Node(1)
    # a Ptr operand: the result is the pointee
    r = through(take_ptr(n))  # tpyc: ok
    r.v = 6
    print("d3 ptr:", n.v)
    s = String("abc")
    # a String operand: the view is a borrow of `s`
    v = view_of(s)  # tpyc: ok
    print("d3 string:", v)
    w = Via()
    m = Node(2)
    # method: the result is the pointee of a Ptr operand
    rm = w.through(take_ptr(m))  # tpyc: ok
    rm.v = 8
    print("d3 method ptr:", m.v)
    s2 = String("xyz")
    # method: the view may be a borrow of a String operand
    vm = w.view_of(s2)  # tpyc: ok
    print("d3 method string:", vm)
