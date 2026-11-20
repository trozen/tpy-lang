# A stub carries one binding decorator: a second `@native` on a method would
# silently replace the first one's native name, call form and contract.
from tpy import int32
from tpy.extern import native


@native("::Holder")
class Holder:
    def __init__(self) -> None: ...

    @native("a")
    @native("b", function=True)
    def g(self) -> int32: ...  # tpyc: error(/Method 'g' cannot have both @native and @native/)


def main() -> None:
    h = Holder()
    print(h.g())


main()
