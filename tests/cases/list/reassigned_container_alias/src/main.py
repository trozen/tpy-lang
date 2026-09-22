# A REASSIGNED alias whose pointee is a CONTAINER binds a reseatable pointer
# local (`std::vector<int32_t>* x = &(a); x = &(b);`) instead of copying, so a
# mutation through the alias is observed on the source it names. One section
# per position and per pointee family; every section reads BOTH sources back.
from tpy import int32


class Node:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    total: int32

    # constructor position, element source
    def __init__(self, rows: list[list[int32]]) -> None:
        x = rows[0]  # tpyc: ok
        x = rows[1]
        x.append(9)
        self.total = len(rows[0]) + len(rows[1])

    # method position, param-name source
    def swap(self, a: list[int32], b: list[int32]) -> None:
        x = a  # tpyc: ok
        x = b
        x.append(9)
        print("method", len(a), len(b), self.total)


# free function, param-name source
def params(a: list[int32], b: list[int32]) -> None:
    x = a  # tpyc: ok
    x = b
    x.append(9)
    print("param", len(a), len(b), len(x))


# free function, local-name source
def locals_() -> None:
    a = [1]
    b = [2, 2]
    x = a  # tpyc: ok
    x = b
    x.append(9)
    print("local", len(a), len(b), len(x))


# free function, element source (the rebound element borrow)
def elements(rows: list[list[int32]]) -> None:
    x = rows[0]  # tpyc: ok
    x = rows[1]
    x.append(9)
    print("elem", len(rows[0]), len(rows[1]))


# inside an if arm: the branch flavor of the same decl
def branch(a: list[int32], b: list[int32], go: bool) -> None:
    if go:
        x = a  # tpyc: ok
        x = b
        x.append(9)
        print("branch", len(a), len(b))


# a dict pointee
def dicts(a: dict[str, int32], b: dict[str, int32]) -> None:
    x = a  # tpyc: ok
    x = b
    x["z"] = 9
    print("dict", len(a), len(b))


# a set pointee
def sets(a: set[int32], b: set[int32]) -> None:
    x = a  # tpyc: ok
    x = b
    x.add(9)
    print("set", len(a), len(b))


# a list-of-records pointee: the element is a reference type too
def records(a: list[Node], b: list[Node]) -> None:
    x = a  # tpyc: ok
    x = b
    x.append(Node(9))
    print("records", len(a), len(b), b[1].n)


# a bytearray pointee: the same reseatable `::tpy::ByteArray*`, in the branch
# flavor too
def bytearrays(a: bytearray, b: bytearray, go: bool) -> None:
    x = a  # tpyc: ok
    if go:
        x = b
    x.append(122)
    print("bytearray", len(a), len(b), len(x))


def main() -> None:
    params([1], [2, 2])
    locals_()
    elements([[1], [2, 2]])
    branch([1], [2, 2], True)
    dicts({"a": 1}, {"b": 2})
    sets({1}, {2, 3})
    records([Node(1)], [Node(2)])
    bytearrays(bytearray(b"a"), bytearray(b"bc"), True)
    bytearrays(bytearray(b"a"), bytearray(b"bc"), False)
    h = Holder([[1], [2, 2]])
    p = [1]
    q = [2, 2]
    h.swap(p, q)


main()
