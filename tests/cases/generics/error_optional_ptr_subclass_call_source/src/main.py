# Sources at a `T | None` pointer slot that are NOT the hoisted-temp shape: a
# field lvalue, and a call returning a SUBCLASS (whose temp class would have to
# be re-derived to avoid slicing). Both reject where an exact rvalue routes.
from tpy import Int32, Own


class Pet:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Dog(Pet):
    def __init__(self, n: Int32) -> None:
        super().__init__(n)


class Holder:
    _val: Pet | None

    def __init__(self, val: Pet | None):
        self._val = val

    def probe(self, val: Pet | None) -> bool:
        return val is not None


def mk_dog() -> Own[Dog]:
    return Dog(1)


def subclass_call_source() -> None:
    c = Holder(None)
    print(c.probe(mk_dog()))  # tpyc: error(/method\.arg_shape/)


def main() -> None:
    subclass_call_source()


main()
