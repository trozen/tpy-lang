# Error: `Box[Own[N]]` over a non-copyable `N` is refused by a member that
# copies, even uncalled, at every formation -- as the hand-written twin.
from typing import Callable
from tpy import Own, Ptr, int32, nocopy
from tplib.rc import Rc
import part_members


@nocopy
class N:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Box[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)

    def get(self) -> T:
        return self.v


class Pair[A, B]:
    a: A
    b: B

    def __init__(self, a: Own[A], b: B) -> None:
        self.a = a
        self.b = b  # tpyc: warning(/may copy B into field/)


class Dup[T]:
    def __init__(self) -> None:
        pass

    # a copy over the class's `T` and the method's own `U` is decided where
    # the call binds `U`: a non-copyable `Pair[N, int32]` refused there
    def dup[U](self, p: Pair[T, U]) -> Own[Pair[T, U]]:
        return p  # tpyc: error(/cannot copy non-copyable type 'Pair\[N, int32\]' into owned storage/)


class Outer[T]:
    b: Box[T]

    def __init__(self, v: T) -> None:
        self.b = Box[T](v)


type Tree[T] = Box[T] | list[Tree[T]]


class Gen[T]:
    v: T
    items: list[T]
    _p: Ptr[T]

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)
        self.items = []
        self._p = Ptr()

    def one(self, x: T) -> int32:
        self.items.append(x)  # tpyc: warning(/may copy T into owned storage/)
        return 1

    def via_genexpr(self) -> int32:
        return sum(self.one(self.v) for _ in range(2))

    def via_ptr(self) -> None:
        self.one(self._p)



class Lone[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)

    def get(self) -> T:
        return self.v

    @staticmethod
    def count(f: Callable[[], T]) -> int32:
        return 1


def mk_n() -> Own[N]:
    return N(9)


# a field's class is formed with its holder
def by_field(o: Outer[Own[N]]) -> None:  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage in 'Box.get' \(main.py:31\)/)
    print(1)


# a recursive alias's body forms the class
def by_tree(t: Tree[Own[N]]) -> None:  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage in 'Box.get' \(main.py:31\)/)
    print(1)


# a generator expression's frame and a handle's referent copy at `Own[N]`
def by_gen(g: Gen[Own[N]]) -> None:  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage in 'Gen.via_genexpr'/) error(/cannot copy non-copyable type 'N' into owned storage in 'Gen.via_ptr'/)
    print(1)


# a subclass forms its base
class Sub(Box[Own[N]]):  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage in 'Box.get' \(main.py:31\)/)
    pass


# an annotation alone forms the class
def ignore(b: Box[Own[N]]) -> None:  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage in 'Box.get'/)
    print(1)


def main() -> None:
    a = Box[Own[P]](P(1))  # tpyc: warning(/copies P into owned storage in 'Box.get' \(main.py:31\)/)
    print(a.v.n)
    b = Box[Own[N]](N(4))  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage in 'Box.get' \(main.py:31\)/)
    print(b.v.n)
    # a tplib getter reading through its `Ptr[T]` copies too
    r = Rc[Own[N]].new(N(5))  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage in 'Rc.get'/)
    print(r.get().n)
    d = Dup[N]()
    p = Pair[N, int32](N(1), 2)
    d.dup[int32](p)
    # an inferred static call forms its class
    own_mk: Callable[[], Own[N]] = mk_n
    print(Lone.count(own_mk))  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage in 'Lone.get'/)
    part_members.main()


main()
