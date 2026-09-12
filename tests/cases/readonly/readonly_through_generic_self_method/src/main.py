# Calling a non-mutating generic method via self.gm() or super().gm() must NOT
# poison auto-readonly inference of the caller. Pre-fix, the ephemeral
# substituted FunctionInfo for the callee defaults to self_mutated=True (or
# mutated_params=None), so the conservative path in Phase 2 wrongly marks the
# caller as self-mutating.
from tpy import int32


class Holder[T]:
    val: T

    def __init__(self, val: T) -> None:
        self.val = val

    def identity[U](self, key: U) -> U:
        return key

    def lookup(self, x: int32) -> int32:
        return self.identity(x)


class SubHolder[T](Holder[T]):
    def __init__(self, val: T) -> None:
        super().__init__(val)

    def super_lookup(self, x: int32) -> int32:
        return super().identity(x)


def main() -> None:
    h = Holder[int32](int32(7))
    print(h.lookup(int32(11)))
    print(h.identity("hello"))

    s = SubHolder[int32](int32(9))
    print(s.super_lookup(int32(13)))


main()
