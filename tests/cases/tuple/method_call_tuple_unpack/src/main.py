# A tuple with a reference element unpacked off a METHOD call (list / dict / ArrayList
# pop, a user method returning Own[tuple]) and off a literal-typed container or local.
from typing import Protocol
from tpy import int32, Own, nocopy
from tplib import ArrayList
import pairs


class R:
    def __init__(self, n: int32) -> None:
        self.n = n


@nocopy
class NC:
    def __init__(self, n: int32) -> None:
        self.n = n


class PairMaker(Protocol):
    def pair(self) -> Own[tuple[list[str], int32]]: ...


class Maker:
    def pair(self) -> Own[tuple[list[str], int32]]:
        return (["x"], 1)

    @staticmethod
    def make() -> Own[tuple[list[str], int32]]:
        return (["s"], 2)


class Stack:
    items: list[tuple[list[str], int32]]

    def __init__(self) -> None:
        self.items = [(["u"], 1)]

    def take(self) -> Own[tuple[list[str], int32]]:
        # The method body returning the popped owned tuple.
        return self.items.pop()  # tpyc: ok

    def drain(self) -> None:
        self.items.append((["m"], 2))
        # Unpacking a method call inside a method body.
        a, n = self.items.pop()  # tpyc: ok
        a.append("y")
        print("method_body", a, n, self.items)


def list_pop() -> None:
    stack: list[tuple[str, list[str], list[str], bool]] = [("s", ["p"], ["q"], True)]
    # list.pop() of a tuple with list elements: the targets alias the popped tuple's.
    a, b, c, d = stack.pop()  # tpyc: ok
    b.append("z")
    print("list_pop", a, b, c, d, stack)


def pop0() -> None:
    stack: list[tuple[list[str], int32]] = [(["x"], 1), (["w"], 2)]
    # list.pop(0)
    a, b = stack.pop(0)  # tpyc: ok
    a.append("y")
    print("pop0", a, b, stack)


def dict_pop() -> None:
    d: dict[str, tuple[list[str], int32]] = {"k": (["x"], 1), "j": (["w"], 2)}
    # dict.pop(key)
    a, b = d.pop("k")  # tpyc: ok
    a.append("y")
    print("dict_pop", a, b, d)


def arraylist_pop() -> None:
    xs = ArrayList[tuple[list[str], int32], 4]()
    xs.append((["x"], 1))
    # A record method (tplib ArrayList.pop) returning the owned tuple.
    a, n = xs.pop()  # tpyc: ok
    a.append("y")
    print("arraylist_pop", a, n, len(xs))


def user_method() -> None:
    s = Stack()
    # A user method declared -> Own[tuple[list[str], int32]].
    a, n = s.take()  # tpyc: ok
    a.append("y")
    print("user_method", a, n, s.items)
    s.drain()


def bound() -> None:
    stack: list[tuple[list[str], int32]] = [(["x"], 1), (["w"], 2)]
    # The popped tuple bound to a local, then unpacked.
    t = stack.pop()  # tpyc: ok
    a, b = t
    a.append("y")
    c, d = t
    print("bound", a, b, c)
    # Rebinding the tuple local and a target.
    t = stack.pop()  # tpyc: ok
    a = ["q"]
    e, f = t
    print("bound", a, e, f, stack)


def literal_stack() -> None:
    stack = [(["x"], 1)]
    # The element type comes from the list literal.
    a, b = stack.pop()  # tpyc: ok
    a.append("y")
    print("literal_stack", a, b, stack)


def literal_stack_bound() -> None:
    stack = [(["x"], 1)]
    t = stack.pop()
    # A literal-typed popped tuple bound, then unpacked.
    a, b = t  # tpyc: ok
    a.append("y")
    print("literal_stack_bound", a, b, t, stack)


def literal_local_tuple() -> None:
    inner = ["x"]
    t = (inner, 1)
    # A literal-typed tuple local: `a` aliases `inner`.
    a, b = t  # tpyc: ok
    a.append("y")
    print("literal_local_tuple", a, b, inner)


def for_head() -> None:
    xs = [(["x"], 1), (["w"], 2)]
    # A for head over a literal-typed list of reference-element tuples.
    for a, b in xs:  # tpyc: ok
        a.append("y")
        print("for_head", a, b)
    print("for_head", xs)


def dict_items_literal() -> None:
    d = {"k": [1], "j": [2]}
    # A for head over a literal-typed dict's items: `v` aliases the dict's value.
    for k, v in d.items():  # tpyc: ok
        v.append(9)
    print("dict_items_literal", d)


def static_method() -> None:
    # A static method returning the owned tuple.
    a, n = Maker.make()  # tpyc: ok
    a.append("y")
    print("static_method", a, n)


def module_qualified() -> None:
    # A module-qualified free call.
    a, n = pairs.pair()  # tpyc: ok
    a.append("y")
    print("module_qualified", a, n)


def protocol_generic[T: PairMaker](maker: T) -> None:
    # A method on a protocol-bound generic receiver.
    a, n = maker.pair()  # tpyc: ok
    a.append("y")
    print("protocol_generic", a, n)


def record_elem() -> None:
    stack: list[tuple[R, int32]] = [(R(1), 1)]
    # A record element aliases the popped tuple's.
    a, b = stack.pop()  # tpyc: ok
    a.n = 9
    print("record_elem", a.n, b, len(stack))


def nocopy_elem() -> None:
    stack: list[tuple[NC, int32]] = []
    stack.append((NC(1), 1))
    # A @nocopy element: compiles only if nothing copies it.
    a, b = stack.pop()  # tpyc: ok
    a.n = 9
    print("nocopy_elem", a.n, b, len(stack))


def main() -> None:
    list_pop()
    pop0()
    dict_pop()
    arraylist_pop()
    user_method()
    bound()
    literal_stack()
    literal_stack_bound()
    literal_local_tuple()
    for_head()
    dict_items_literal()
    static_method()
    module_qualified()
    protocol_generic(Maker())
    record_elem()
    nocopy_elem()


main()
