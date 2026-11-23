# Error: a `readonly[T]` field at `T = Own[P]` holds a `readonly[P]`, as the
# hand-written twin's `v: readonly[P]`: a write through it is refused.
from tpy import Own, int32, readonly


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Ro[T]:
    v: readonly[T]

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)


def main() -> None:
    r = Ro[Own[P]](P(2))
    r.v.n = 4  # tpyc: error(/Cannot mutate readonly reference/)
    print(r.v.n)


main()
