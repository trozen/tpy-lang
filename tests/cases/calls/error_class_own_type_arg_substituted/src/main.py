# A generic body holding `C[T]` (a class written in TPy) refuses the call
# instantiating it at `T = Own[P]`, spelled or inferred, as a tuple leaf
# does: sema forms `C[P]` there, while the generic's C++ body would
# instantiate the class at the owned tag; until unit 2b the call is
# refused (BUGS.md#class-own-type-arg-method-result-ill-formed).
from tpy import Fn, Own, int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class C[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def get(self) -> T:
        return self.items[0]


def make_c[T](x: T) -> Own[C[T]]:
    c = C[T]()
    c.items.append(x)
    return c


def make_c_from[A, T](f: Fn[[A], T], k: A) -> Own[C[T]]:
    c = C[T]()
    c.items.append(f(k))
    return c


def mk(k: int32) -> Own[P]:
    return P(k)


def main() -> None:
    # the inferred twin: `mk` binds `T = Own[P]`
    print(len(make_c_from(mk, 4).items))  # tpyc: error(/'make_c_from' infers `T = Own\[P\]` from 'mk', which is not supported yet: 'T' appears inside 'Own\[C\[T\]\]'/)
    print(make_c[Own[P]](P(3)).get().x)  # tpyc: error(/`Own\[P\]` as a type argument of 'make_c' is not supported yet: 'T' appears inside 'Own\[C\[T\]\]'/)


main()
