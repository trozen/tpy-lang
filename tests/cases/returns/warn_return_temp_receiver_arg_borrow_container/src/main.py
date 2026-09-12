# The CONTAINER payload of warn_return_temp_receiver_arg_borrow: a method on a
# temporary receiver borrowing from an ARGUMENT at an `Own[list[int32]]`
# return copies and warns. The copy is the ACKNOWLEDGED CPython divergence --
# an append to `h.items` after the return would show in the result there and
# not here -- so the case prints only what both agree on.
from tpy import int32, Own, copy


class Holder:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]


class Finder:
    def __init__(self) -> None:
        pass

    def pick(self, h: Holder) -> list[int32]:
        # Borrows from ARGUMENT 0.
        return h.items


def take(h: Holder) -> Own[list[int32]]:
    return Finder().pick(h)  # tpyc: warning(/copies list\[int32\] into owned storage/)


def take_copy(h: Holder) -> Own[list[int32]]:
    return copy(Finder().pick(h))  # tpyc: ok


def main() -> None:
    h = Holder()
    print(len(take(h)), len(take_copy(h)), len(h.items))


main()
