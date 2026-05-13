# Empty {} picks up dict[K, V] from the LHS in field assign, function arg,
# return-with-Own, and nested dict literal positions -- not just from a
# local annotation.
from tpy import Own


class Box:
    by_name: dict[str, int]
    by_pair: dict[str, dict[str, int]]

    def __init__(self) -> None:
        self.by_name = {}
        self.by_pair = {"first": {}}


def take(d: dict[str, int]) -> int:
    return len(d)


def make() -> Own[dict[str, int]]:
    return {}


def main() -> None:
    b = Box()
    b.by_name["x"] = 1
    print(b.by_name["x"], len(b.by_pair), len(b.by_pair["first"]))
    print(take({}))
    fresh = make()
    fresh["y"] = 2
    print(fresh["y"])


main()
