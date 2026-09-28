# A multi-base __init__ that skips an __init__-less base with no C++ default
# constructor is rejected, as for a single base (LANGUAGE_FEATURES "Multiple
# Inheritance (D22)", __init__ coverage).
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


class Both(Holder, Named):
    # Holder is skipped, and a field without a default constructor blocks its own
    def __init__(self) -> None:  # tpyc: error(/does not initialize base 'Holder', but 'Holder' cannot be constructed without arguments/)
        self.name = "b"


def main() -> None:
    b = Both()
    print(b.name)


main()
