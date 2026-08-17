# A user record inheriting dict / set: the undeclared methods dispatch to the
# builtin stubs, and the record keeps reference semantics across a call.
from tpy import Int32, StrView


class Counter(dict[StrView, Int32]):
    label: str

    def __init__(self, label: str) -> None:
        self.label = label


class Tags(set[Int32]):
    def __init__(self) -> None:
        pass


def bump(c: Counter, key: StrView) -> None:
    # Mutating through the parameter must be visible to the caller -- a copy
    # here would still print identically for reads alone, hiding the defect.
    c[key] = c.get(key, 0) + 1


def add_tag(t: Tags, tag: Int32) -> None:
    t.add(tag)


def main() -> None:
    c = Counter("hits")
    c["a"] = 1  # inherited __setitem__ on a record receiver
    bump(c, "a")
    bump(c, "b")
    print(c.label, len(c), c["a"], c["b"])

    t = Tags()
    t.add(7)  # inherited set.add on a record receiver
    add_tag(t, 8)
    t.add(7)  # duplicate: set semantics keep the size at 2
    print(len(t))
    t.discard(7)
    print(len(t))


main()
