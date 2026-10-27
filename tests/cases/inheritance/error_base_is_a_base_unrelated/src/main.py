# A class that does not derive from dict is still refused at a dict param:
# the base relation admits subclasses only.
from tpy import int32


class Other:
    n: int32

    def __init__(self) -> None:
        self.n = 1


def size(m: dict[str, int32]) -> int32:
    return len(m)


def main() -> None:
    o = Other()
    print(size(o))  # tpyc: error(/Type mismatch in argument 'm': expected dict\[str, int32\], got Other/)


main()
