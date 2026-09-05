# A match class-pattern keyword capture binds an `auto&` alias into the
# subject's field, and the family it admits is the reference axis -- so an
# Array field joins the list and dict fields that already bound.
from tpy import Int32, Array


class H:
    arr: Array[Int32, 2]
    xs: list[Int32]
    d: dict[str, Int32]

    def __init__(self) -> None:
        self.arr = Array[Int32, 2]()
        self.xs = [1]
        self.d = {"a": 1}


def main() -> None:
    # A LOCAL subject, not a param: a param subject renders `const H&` and the
    # capture's hoisted pointer drops the const
    # (BUGS.md#match-capture-mutation-not-credited, which the list field hits
    # identically). No `set` leg either: a set FIELD capture rejects at
    # `stmt.match` (BUGS.md#match-capture-set-field).
    h = H()
    match h:
        case H(arr=a, xs=v, d=m):  # tpyc: ok
            # Mutating through each capture and reading `h` afterwards is what
            # separates an alias from a copy; a read-only arm would match
            # CPython either way.
            a[0] = 7
            v.append(2)
            m["b"] = 3
    print(h.arr[0], len(h.xs), len(h.d))


main()
