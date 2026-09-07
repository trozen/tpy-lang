# The CONTAINER payload of warn_return_readonly_own_borrow_call, with the
# FIELD source (its twin uses a call): `readonly[Own[list[Int32]]]` returning
# `self.items` copies and warns. The copy is the ACKNOWLEDGED CPython
# divergence -- an append to `h.items` after the return would show in the
# result there and not here -- so the case prints only what both agree on and
# the WARNING is the pin.
from tpy import Int32, Own, readonly, copy


class Holder:
    items: list[Int32]

    def __init__(self) -> None:
        self.items = [1, 2]

    def grab(self) -> readonly[Own[list[Int32]]]:
        return self.items  # tpyc: warning(/copies list\[Int32\] into owned storage/)

    def grab_copy(self) -> readonly[Own[list[Int32]]]:
        return copy(self.items)  # tpyc: ok


def main() -> None:
    h = Holder()
    print(len(h.grab()), len(h.grab_copy()), len(h.items))


main()
