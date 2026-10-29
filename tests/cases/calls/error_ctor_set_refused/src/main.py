# A class takes one TPy-bodied __init__ (docs/LANGUAGE_FEATURES.md, "a class
# takes one `__init__` with a TPy body"): typing.overload stubs on __init__
# are refused at the class, even when nothing constructs it.
from typing import overload
from tpy import int32


class Q:
    v: int32

    # The first stub is the subject: a construction names what it builds.
    @overload
    def __init__(self, x: int32) -> None: ...  # tpyc: error(/cannot be @overload/)

    @overload
    def __init__(self, x: str) -> None: ...

    def __init__(self, x: int32 | str) -> None:
        self.v = len(x) if isinstance(x, str) else x


def main() -> None:
    print("unreached")


main()
