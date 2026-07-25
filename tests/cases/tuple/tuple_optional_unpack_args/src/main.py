# Arg-passing out of a pointer-repr Optional[record] tuple-unpack target
# (`a, b = p` off a `tuple[T | None, T | None]` param, where each target
# binds a nullable pointer local): a NARROWED target into a plain-record
# param and an UN-narrowed target into a `T | None` param, from both a
# const-inferred source (read-only body) and a mutable one. The callees
# mutate through the borrow and main observes it, so a silent copy at
# either boundary would change the printed output.
from tpy import Int32, readonly


class T:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def peek(t: readonly[T]) -> Int32:
    return t.x


def bump(t: T) -> None:
    t.x = t.x + 1


def bump_opt(t: T | None) -> None:
    if t is not None:
        t.x = t.x + 10


def read_first(p: tuple[T | None, T | None]) -> Int32:  # tpyc: ok
    # Read-only body -- slots stay const, so the targets bind `const T*`
    # and the narrowed one passes into a readonly param.
    a, _ = p
    if a is not None:
        return peek(a)
    return 0


def apply(p: tuple[T | None, T | None]) -> None:  # tpyc: ok
    # Mutates through the callees -- slots stay mutable (`T*` targets).
    a, b = p
    if a is not None:
        bump(a)
    bump_opt(b)


def main() -> None:
    first = T(1)
    second = T(2)

    print(read_first((first, second)))
    apply((first, second))
    print(first.x)
    print(second.x)

    apply((first, None))
    print(first.x)
    print(read_first((None, second)))


main()
