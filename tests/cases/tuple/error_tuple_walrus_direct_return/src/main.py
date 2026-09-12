# A walrus hands out its value: returning the walrus expression directly
# (`return (t := items[0])`) is the same storage-rooted escape as binding
# then returning by name, and is rejected the same way.
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def pick() -> tuple[int32, Box]:
    items: list[tuple[int32, Box]] = [(1, Box(5))]
    return (t := items[0])  # tpyc: error(/storage owned by the function/)


def main() -> None:
    pass


main()
