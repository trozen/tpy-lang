# The VALUE half of the instantiation rule: a bare-`T` getter is spelled
# `val_or_ref_t<T>`, so at a VALUE argument it hands its result back by value
# and a frame materializes it into `__for_src_N`.
# The reference half -- the same getter at a container argument, which aliases
# on both routes -- is pinned by generics/inherited_accessor_subst.
# The `decl` section pins the OTHER end of the same instantiation rule: at
# `T = str` the getter returns by value, so the local must OWN a `std::string`.
# Spelling it `std::string_view` over that return printed garbage past the SSO
# buffer, which is what a monomorphic `-> str` getter never did.
from typing import Iterator

from tpy import int32


class Cell[T]:
    v: T

    def __init__(self, v: T) -> None:
        # unavoidable on a generic field store: T may be a reference type, so
        # the store is a copy the compiler cannot prove away
        self.v = v  # tpyc: warning(/may copy T into field/)

    @property
    def payload(self) -> T:
        return self.v

    # the spelled twin at the same instantiation
    def payload_m(self) -> T:
        return self.v


# generator whose loop body suspends, `T` bound to `str`
def chars(c: Cell[str]) -> Iterator[int32]:
    n = 0
    for ch in c.payload:  # tpyc: ok
        print("prop_char:", ch)
        n += 1
        yield n


# the method spelling of the same read, to show the two agree
def chars_m(c: Cell[str]) -> Iterator[int32]:
    n = 0
    for ch in c.payload_m():  # tpyc: ok
        print("meth_char:", ch)
        n += 1
        yield n


# local decl at `T = str`: the read is by value, so the local OWNS the string
def decl(c: Cell[str]) -> None:
    p = c.payload  # tpyc: ok
    print("decl:", p)


def main() -> None:
    decl(Cell[str]("a string long enough to leave the SSO buffer behind"))
    for n in chars(Cell[str]("ab")):
        print("prop:", n)
    for n in chars_m(Cell[str]("ab")):
        print("meth:", n)


main()
