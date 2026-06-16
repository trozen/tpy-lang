# obj[key] dispatches to a user-defined __getitem__ whose key is non-integer
# (str here), instead of rejecting with "subscript must be an integer". The
# index type is validated against the __getitem__ key param.
from tpy import Int32


class Scores:
    _a: Int32
    _b: Int32

    def __init__(self) -> None:
        self._a = 10
        self._b = 20

    def __getitem__(self, key: str) -> Int32:
        if key == "a":
            return self._a
        return self._b


def main() -> None:
    s = Scores()
    print(s["a"], s["b"])


main()
