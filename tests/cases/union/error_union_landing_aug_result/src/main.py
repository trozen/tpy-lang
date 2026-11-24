# An in-place fallback stores the reflected dunder's result into the target
# as `v = v + R2()` would: a result of another type is refused.
from tpy import int32


class Base:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Mid(Base):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


class R2:
    def __init__(self) -> None:
        pass

    def __radd__(self, other: Base | Mid) -> int32:
        if isinstance(other, Mid):
            return other.n + 10
        return other.n + 20


def main() -> None:
    v = Mid(5)
    v += R2()  # tpyc: error(/Type mismatch in '\+=' to 'v': expected Mid, got int32/)
    print(v.n)


main()
