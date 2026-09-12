# Optional[generic @dynamic protocol] is allowed at a parameter position --
# the parameterized form lowers to `const Container<int32_t>*` just like the
# non-generic case.
from typing import Protocol, Optional
from tpy import int32, dynamic


@dynamic
class Container[T](Protocol):
    def get(self) -> T: ...


class IntBox(Container[int32]):
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def get(self) -> int32:
        return self.v


def maybe_show(c: Optional[Container[int32]]) -> int32:
    if c is None:                 # tpyc: ok
        return -1
    return c.get()


def main() -> None:
    print(maybe_show(IntBox(42)))
    print(maybe_show(None))


main()
