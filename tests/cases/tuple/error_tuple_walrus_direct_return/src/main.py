# A walrus hands out its value: returning the walrus expression directly
# (`return (t := items[0])`) is the same storage-rooted escape as binding
# then returning by name, and is rejected the same way.
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def pick() -> tuple[Int32, Box]:
    items: list[tuple[Int32, Box]] = [(1, Box(5))]
    return (t := items[0])  # tpyc: error(/storage owned by the function/)


def main() -> None:
    pass


main()
