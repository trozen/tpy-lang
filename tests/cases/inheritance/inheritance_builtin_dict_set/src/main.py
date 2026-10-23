# A user record inheriting dict / set: the undeclared methods dispatch to the
# builtin stubs, and the record keeps reference semantics across a call.
from tpy import int32, StrView


class Counter(dict[StrView, int32]):
    label: str

    def __init__(self, label: str) -> None:
        self.label = label


class Tags(set[int32]):
    def __init__(self) -> None:
        pass


def bump(c: Counter, key: StrView) -> None:
    # Mutating through the parameter must be visible to the caller -- a copy
    # here would still print identically for reads alone, hiding the defect.
    c[key] = c.get(key, 0) + 1


def add_tag(t: Tags, tag: int32) -> None:
    t.add(tag)


def missing(c: Counter) -> None:
    # An inherited `__getitem__` is the dict's own lookup: a missing key
    # raises and inserts nothing.
    try:
        print(c["zz"])  # tpyc: ok
    except KeyError:
        print("missing:", len(c))


def missing_elem(cs: list[Counter]) -> None:
    # A container-element receiver: the inherited lookup on `cs[0]` raises
    # for a missing key and inserts nothing.
    try:
        print(cs[0]["zz"])  # tpyc: ok
    except KeyError:
        print("missing_elem: KeyError")


def main() -> None:
    c = Counter("hits")
    c["a"] = 1  # inherited __setitem__ on a record receiver
    bump(c, "a")
    bump(c, "b")
    print(c.label, len(c), c["a"], c["b"])
    missing(c)
    cs: list[Counter] = []
    cs.append(Counter("elems"))
    missing_elem(cs)

    t = Tags()
    t.add(7)  # inherited set.add on a record receiver
    add_tag(t, 8)
    t.add(7)  # duplicate: set semantics keep the size at 2
    print(len(t))
    t.discard(7)
    print(len(t))


main()
