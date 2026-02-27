# Field ordering: struct layout matches __init__ assignment order, not annotation order.
# Prevents C++ -Wreorder-ctor warnings.
from tpy import Int32


class Rect:
    height: Int32

    def __init__(self, width: Int32, height: Int32):
        self.width = width
        self.height = height


def main() -> None:
    r = Rect(Int32(5), Int32(10))
    print(r.width)
    print(r.height)


main()
