# Mix of annotated and auto-declared fields
from tpy import Int32


class Rect:
    width: Int32

    def __init__(self, width: Int32, height: Int32):
        self.width = width
        self.height = height


def main() -> None:
    r = Rect(Int32(5), Int32(10))
    print(r.width)
    print(r.height)


main()
