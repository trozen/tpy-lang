# isinstance across multi-base hierarchy folds to True for every base in the MRO.
from tpy import int32


class Named:
    name: str


class Counted:
    count: int32


class Widget(Named, Counted):
    def __init__(self, name: str, count: int32) -> None:
        self.name = name
        self.count = count


def main() -> None:
    w = Widget("x", int32(1))
    # Both folds resolve at compile time via MRO membership.
    if isinstance(w, Named):  # tpyc: ok
        print("isa Named")
    if isinstance(w, Counted):  # tpyc: ok
        print("isa Counted")
    if isinstance(w, Widget):  # tpyc: ok
        print("isa Widget")


main()
