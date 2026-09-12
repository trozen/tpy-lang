# Mix of annotated and auto-declared fields
from tpy import int32


class Rect:
    width: int32

    def __init__(self, width: int32, height: int32):
        self.width = width
        self.height = height


def main() -> None:
    r = Rect(int32(5), int32(10))
    print(r.width)
    print(r.height)


main()
