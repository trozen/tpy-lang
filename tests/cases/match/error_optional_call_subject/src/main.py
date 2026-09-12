# A CALL subject whose type is Optional keeps rejecting: the optional tiers
# lift the subject to a pointer, which over a temporary would dangle.
from typing import Optional

from tpy import int32, Own


class Box:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def pick() -> Own[Optional[Box]]:
    return Box(1)


def main() -> None:
    # The subject is an rvalue, so there is no storage for the tier's `T*`.
    match pick():  # tpyc: error(/stmt\.match/)
        case None:
            print("none")
        case _:
            print("some")


main()
