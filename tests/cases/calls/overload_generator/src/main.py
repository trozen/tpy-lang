# An @overload-ed generator: the impl carries the `yield`, the stubs declare
# the two callable arms. Both arms are exercised.
from typing import overload, Iterator
from tpy import Int32


@overload
def rep[T](obj: T) -> Iterator[T]: ...
@overload
def rep[T](obj: T, n: Int32) -> Iterator[T]: ...
def rep[T](obj: T, n: Int32 = -1) -> Iterator[T]:
    i = 0
    while n < 0 or i < n:
        yield obj  # tpyc: ok
        i += 1


def mk() -> str:
    # an rvalue SOURCE, so the argument is a temporary rather than a literal
    return "yo"


def main():
    # The arguments are rvalues on purpose: the generator's frame borrows its
    # `T` slot past the statement, so each one has to be hoisted into a named
    # temp whatever route resolved the call -- an overload's per-signature fi
    # carries concrete params, which used to hide the slot entirely.
    # one-arg arm (unbounded), consumed with a manual break
    count = 0
    for x in rep("hi"):
        print(x)
        count += 1
        if count == 2:
            break
    # two-arg arm (bounded)
    for y in rep(9, 3):
        print(y)
    # ... and an rvalue that is a CALL, not a literal. The warning is spurious
    # -- the temp below outlives the loop -- and filed as
    # BUGS.md#gen-iter-arg-temp-warns-though-hoisted
    for z in rep(mk(), 2):  # tpyc: warning(/borrows from temporary argument/)
        print(z)


main()
