# Field ordering: struct layout matches __init__ assignment order, not annotation order.
# Prevents C++ -Wreorder-ctor warnings.
from tpy import int32


class Rect:
    height: int32

    def __init__(self, width: int32, height: int32):
        self.width = width
        self.height = height


def main() -> None:
    r = Rect(int32(5), int32(10))
    print(r.width)
    print(r.height)


main()
