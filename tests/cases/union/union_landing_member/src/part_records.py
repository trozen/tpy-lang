# Record values landing in a union of their bases: the most derived base
# the value upcasts to (the upcast warning names it), as a local and as an
# argument, the dominant one of several bases. The object itself is lent,
# so a write through the union shows on the original. A field store of a
# record reads its source as the member it lands in; an operator dunder
# taking such a union takes a union value as it is; a membership needle
# compares with each element as the base member it lands in.
from tpy import copy


class Base:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


class Mid(Base):
    def __init__(self, n: int) -> None:
        super().__init__(n)


class Leaf(Mid):
    def __init__(self, n: int) -> None:
        super().__init__(n)


def bump(v: Base | Mid) -> None:
    if isinstance(v, Mid):
        v.n += 100
    else:
        v.n += 1


# local: a Leaf lands in Mid, the base no other candidate derives from
def local_chain() -> None:
    leaf = Leaf(4)
    v: Base | Mid = leaf  # tpyc: warning(/upcast narrows 'Leaf' to 'Mid'/)
    bump(v)
    print("records-local", leaf.n)


# argument: the same rule at a parameter
def arg_chain() -> None:
    leaf = Leaf(5)
    bump(leaf)  # tpyc: ok
    print("records-arg", leaf.n)


class A:
    def __init__(self) -> None:
        pass


class B:
    def __init__(self) -> None:
        pass


class AB(A, B):
    def __init__(self) -> None:
        A.__init__(self)
        B.__init__(self)


class ABLeaf(AB):
    def __init__(self) -> None:
        super().__init__()


def dominant(u: A | B | AB) -> None:
    print("records-dominant", isinstance(u, AB))


class Cat:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


class Dog:
    y: int

    def __init__(self, y: int) -> None:
        self.y = y


class Pen:
    u: Cat | Dog

    def __init__(self, u: Cat | Dog) -> None:
        self.u = copy(u)

    def has_cat(self) -> bool:
        u = self.u
        return isinstance(u, Cat)


g_cat: Cat = Cat(10)


class Acc:
    def __init__(self) -> None:
        pass

    def __add__(self, other: Base | Mid) -> int:
        if isinstance(other, Mid):
            return other.n
        return 0

    def __iadd__(self, other: Base | Mid) -> "Acc":
        print("records-iadd", isinstance(other, Mid))
        return self


# operator dunder at a pointer-variant parameter: a union value is passed
# on as it is, a local alias of one too, and a subclass object is lent as
# its member at the binary and the in-place dunder alike
def operators(x: Base | Mid) -> None:
    a = Acc()
    print("records-operand", a + x)  # tpyc: ok
    u = x
    print("records-operand-alias", a + u)  # tpyc: ok
    leaf = Leaf(9)
    print("records-operand-object", a + leaf)  # tpyc: ok
    # the operand is the object itself, not a copy of it
    leaf.n = 12
    print("records-operand-object-after", a + leaf)  # tpyc: ok
    a += leaf  # tpyc: ok


# field stores: from a global, from a ternary's fresh arm, from a reseated
# local -- each source read as the Cat member
def stores(flag: bool, c2: Cat) -> None:
    p = Pen(Dog(5))
    # a global stays reachable after the store, so the field holds a copy
    p.u = g_cat  # tpyc: warning(/copies Cat into field/)
    print("records-store-global", p.has_cat())
    p.u = Dog(6)
    # the parameter arm stays the caller's, so the select is copied whole
    p.u = Cat(1) if flag else c2  # tpyc: warning(/copies Cat into field/)
    print("records-store-select", p.has_cat())
    c = Cat(1)
    c = Cat(2)
    p.u = c  # tpyc: ok
    print("records-store-local", p.has_cat())


class EqBase:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def __eq__(self, other: "EqBase") -> bool:
        return self.n == other.n


class EqMid(EqBase):
    def __init__(self, n: int) -> None:
        super().__init__(n)


class EqLeaf(EqMid):
    def __init__(self, n: int) -> None:
        super().__init__(n)


# membership needle: a record compared with the union elements by value
def needle() -> None:
    xs: list[EqBase | EqMid] = [EqMid(1), EqBase(3)]
    leaf = EqLeaf(1)
    m = EqMid(3)
    print("records-needle", leaf in xs, m in xs, EqBase(4) in xs)  # tpyc: ok


def main() -> None:
    needle()
    local_chain()
    arg_chain()
    dominant(ABLeaf())  # tpyc: ok
    stores(True, Cat(3))
    operators(Mid(17))
