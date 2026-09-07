# A keyword capture of an `Optional` field in a SYNC body: only the frame
# flavour registers a pointer member for the captured name.
from typing import Optional
from tpy import Int32


class Inner:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Box:
    maybe: Optional[Inner]

    def __init__(self, n: Int32) -> None:
        self.maybe = Inner(n)


def sync_cap(b: Box) -> None:
    # `v` captures an optional field outside a frame.
    match b:  # tpyc: error(/stmt\.match/)
        case Box(maybe=v):
            if v is not None:
                print(v.n)


def main() -> None:
    sync_cap(Box(3))


main()
