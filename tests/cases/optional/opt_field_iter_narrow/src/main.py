# Bare iteration over a narrowed-Optional FIELD (dict/list/set/str
# receivers): the narrowed field must render exactly one Optional unwrap in
# the for-iterable position (a doubled unwrap derefs the contained value).

from tpy import Int32


class Holder:
    d: dict[str, str] | None
    lst: list[Int32] | None
    st: set[Int32] | None
    s: str | None

    def __init__(self):
        self.d = {"transfer-encoding-extension": "1", "b": "2"}
        self.lst = [1, 2, 3]
        self.st = {40}
        self.s = "xy"

    def scan_dict(self) -> Int32:
        n = 0
        if self.d is not None:
            for k in self.d:
                if k == "transfer-encoding-extension":
                    n += 1
        return n

    def sum_list(self) -> Int32:
        n = 0
        if self.lst is not None:
            for x in self.lst:
                n += x
        return n

    def sum_set(self) -> Int32:
        n = 0
        if self.st is not None:
            for x in self.st:
                n += x
        return n

    def count_str(self) -> Int32:
        n = 0
        if self.s is not None:
            for c in self.s:
                n += 1
        return n


def main() -> None:
    h = Holder()
    print(h.scan_dict())
    print(h.sum_list())
    print(h.sum_set())
    print(h.count_str())


main()
