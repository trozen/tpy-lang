# A generator binding an `Optional[dict]` field to a local, mutating it
# through that local, and re-seating the local to None across a suspension.
from typing import Iterator
from tpy import Int32


class Holder:
    d: dict[Int32, Int32] | None

    def __init__(self) -> None:
        self.d = {1: 10, 2: 20}

    def keys_of(self) -> Iterator[Int32]:
        m = self.d
        if m is not None:
            # The local aliases the field's dict -- the insert must be seen
            # by the owner after the generator drains.
            m[3] = 30
            for k in m:
                yield k
        m = None
        if m is None:
            yield -1


def main() -> None:
    h = Holder()
    print(sum(h.keys_of()))
    d = h.d
    if d is not None:
        print(len(d))


main()
