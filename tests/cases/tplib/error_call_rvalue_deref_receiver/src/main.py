# A CALL rvalue as the receiver in front of a Box `__deref__`: the deref
# receiver rows admit a bound name, not a temporary, so `mk().speak()` is
# rejected.
from tpy import Int32, Own
from tplib import Box


class Pet:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def speak(self) -> Int32:
        return self.n


def mk() -> Own[Box[Pet]]:
    return Box(Pet(3))


def use() -> Int32:
    return mk().speak()  # tpyc: error(/method.marker.deref.recv_shape/)


def main() -> None:
    print(use())


main()
