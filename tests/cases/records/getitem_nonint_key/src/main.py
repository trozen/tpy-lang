# obj[key] dispatches to a user-defined __getitem__ whose key is non-integer
# (str here), instead of rejecting with "subscript must be an integer". The
# index type is validated against the __getitem__ key param.
from tpy import int32


class Scores:
    _a: int32
    _b: int32

    def __init__(self) -> None:
        self._a = 10
        self._b = 20

    def __getitem__(self, key: str) -> int32:
        if key == "a":
            return self._a
        return self._b


# A str *variable* (a view, not a literal) as the index: the synthesized
# operator[] must take the borrow form (string_view), or a view key won't
# convert. A string literal converts to either form, so this guards the fix.
def lookup(s: Scores, key: str) -> int32:
    return s[key]


def main() -> None:
    s = Scores()
    print(s["a"], s["b"])
    k = "a"
    print(lookup(s, k), lookup(s, "b"))


main()
