# A multi-base @dataclass over an __init__-less base with no C++ default
# constructor is rejected; the advice does not name an __init__ it lacks.
from dataclasses import dataclass
from tpy import int32, nocopy


@nocopy
class Handle:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __del__(self) -> None:
        pass


class Holder:
    h: Handle


class Named:
    name: str = ""


# The synthesized __init__ skips Holder, whose 'h' cannot be built without arguments.
@dataclass
class Both(Named, Holder):  # tpyc: error(/does not initialize parent class 'Holder'.*Either give 'Holder' field defaults, or give it an '__init__'/)
    z: int32


def main() -> None:
    b = Both(3)
    print(b.z)


main()
