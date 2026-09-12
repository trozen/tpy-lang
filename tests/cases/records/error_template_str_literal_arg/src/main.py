# A str LITERAL at a template-expanded dunder slot raises the view-vs-owned
# parameter question the expansion does not answer, so it keeps rejecting.
from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __eq__(self, other: str) -> bool:
        return self.n == len(other)


def main() -> None:
    b = Box(3)
    print(b.__eq__("abc"))  # tpyc: error(/method.ptr_template.arg_shape/)


main()
