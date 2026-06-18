# obj[k]=v / del obj[k] on a user class with a non-int (str) key dispatch to the
# user __setitem__/__delitem__ (written value read back); int-keyed record +
# containers confirm no over-trigger.
from tpy import Int32


class Store:
    _a: Int32
    _b: Int32

    def __init__(self) -> None:
        self._a = 0
        self._b = 0

    def __getitem__(self, key: str) -> Int32:
        if key == "a":
            return self._a
        return self._b

    def __setitem__(self, key: str, value: Int32) -> None:
        if key == "a":
            self._a = value
        else:
            self._b = value

    def __delitem__(self, key: str) -> None:
        if key == "a":
            self._a = -1
        else:
            self._b = -1


class IntBox:
    v: Int32

    def __init__(self) -> None:
        self.v = 0

    def __getitem__(self, i: Int32) -> Int32:
        return self.v

    def __setitem__(self, i: Int32, value: Int32) -> None:
        self.v = i + value

    def __delitem__(self, i: Int32) -> None:
        self.v = -i


def main() -> None:
    s = Store()
    s["a"] = 10            # str-key __setitem__
    s["b"] = 20
    print(s["a"], s["b"])  # 10 20 -- written values read back
    del s["a"]             # str-key __delitem__
    print(s["a"])          # -1

    # int-keyed user record still dispatches (no over-trigger)
    k = IntBox()
    k[3] = 4
    print(k[0])            # 7
    del k[5]
    print(k[0])            # -5

    # containers unaffected
    xs: list[Int32] = [1, 2, 3]
    xs[1] = 99
    del xs[0]
    print(xs[0], len(xs))  # 99 2
    d: dict[str, Int32] = {}
    d["x"] = 5
    del d["x"]
    print(len(d))          # 0


main()
