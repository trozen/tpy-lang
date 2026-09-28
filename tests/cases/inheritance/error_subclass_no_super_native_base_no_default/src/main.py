# A hand-written `__init__` over an `__init__`-less `@native` base that has no
# default constructor is rejected like one over a TPy base: skipping the parent
# call would leave the base unbuilt (LANGUAGE_FEATURES "Single class inheritance").
from tpy import int32
from tpy.extern import native


@native("CppCounter")
class Counter:
    value: int32

    def __init__(self, value: int32) -> None: ...


@native("CppHolder")
class Holder:
    c: Counter


class Slot(Holder):
    z: int32

    # no parent call, and `Holder`'s `Counter` field cannot be default-built
    def __init__(self, z: int32) -> None:  # tpyc: error(/'Slot.__init__' does not initialize base 'Holder', but 'Holder' cannot be constructed without arguments/)
        self.z = z


def main() -> None:
    s = Slot(3)
    print(s.z)


main()
