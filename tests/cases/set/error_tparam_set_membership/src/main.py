# Membership against a `set[T]` receiver inside a generic record: the resolved
# __contains__ lane has no widened key row for an open type param, so `x in
# self._seen` is rejected.
from tpy import Hashable


class Seen[T: Hashable]:
    _seen: set[T]

    def __init__(self) -> None:
        self._seen = set()

    def add(self, x: T) -> None:
        self._seen.add(x)

    def has(self, x: T) -> bool:
        return x in self._seen  # tpyc: error(/binop.shape.in.tparam/)


def main() -> None:
    s = Seen[str]()
    s.add("a")
    print(s.has("a"))


main()
