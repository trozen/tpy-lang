# A CALL rvalue as the receiver in front of a Box `__deref__`: the deref
# receiver rows admit a bound name, not a temporary, so `mk().speak()` is
# rejected.
from tpy import int32, Own
from tplib import Box


class Pet:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def speak(self) -> int32:
        return self.n


def mk() -> Own[Box[Pet]]:
    return Box(Pet(3))


def use() -> int32:
    return mk().speak()  # tpyc: error(/method.marker.deref.recv_shape/)


def main() -> None:
    print(use())


main()
