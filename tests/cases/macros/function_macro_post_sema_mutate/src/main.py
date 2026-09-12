# A post-sema deferred macro rewrites sentinel(c) -> c.bump() (a MUTATING call
# on the param receiver) and calls note_param_mutated, so Phase-2 emits the
# param mutable (Counter&) instead of const& -- the mutating call then compiles.
# The mutation is observed across two calls on the shared Counter: 1 then 2.
from mutatemod import resolve_bump
from tpy import int32


class Counter:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def bump(self) -> int32:
        self.n += 1
        return self.n


def sentinel(c: Counter) -> int32:
    return 0


@resolve_bump
def poke(c: Counter) -> int32:
    return sentinel(c)


def main() -> None:
    c = Counter()
    print(poke(c))
    print(poke(c))


main()
