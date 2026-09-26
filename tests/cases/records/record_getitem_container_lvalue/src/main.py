# A user `__getitem__` returning a container by reference is a container lvalue
# rooted in the receiver, like a builtin nested element; `__setitem__` takes one in.
from typing import Callable, Iterator
from tpy import Hashable, Own, ValueType, copy, int32, readonly
from tplib.array_list import ArrayList


class DefaultDict[K: Hashable, V]:
    factory: Callable[[], V]
    _data: dict[K, V]

    def __init__(self, factory: Callable[[], V]) -> None:
        self.factory = factory
        self._data = {}

    # Inserts on a miss, so it mutates the receiver.
    @readonly(False)
    def __getitem__(self, key: K) -> V:
        if key not in self._data:
            self._data[copy(key)] = self.factory()
        return self._data[key]

    def __setitem__(self, key: K, value: Own[V]) -> None:
        self._data[key] = value

    def __len__(self) -> int32:
        return len(self._data)


class Rows:
    rows: list[list[int32]]

    def __init__(self) -> None:
        self.rows = [[1], [2]]

    def __getitem__(self, i: int32) -> list[int32]:
        return self.rows[i]


class Box[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def add(self, v: Own[T]) -> None:
        self.items.append(v)

    def __getitem__(self, i: int32) -> T:
        return self.items[i]


class Pt:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Pts:
    ps: list[Pt]

    def __init__(self) -> None:
        self.ps = [Pt(1), Pt(2)]

    def __getitem__(self, i: int32) -> Pt:
        return self.ps[i]


def new_list() -> Own[list[int32]]:
    return []


def new_set() -> Own[set[int32]]:
    return set()


def free_function() -> None:
    g: DefaultDict[str, list[int32]] = DefaultDict(new_list)
    g["a"].append(1)
    print("free len:", len(g["a"]))  # tpyc: ok
    x = g["a"]  # tpyc: ok
    x.append(2)
    print("free alias:", g["a"])  # tpyc: ok
    for v in g["a"]:  # tpyc: ok
        print("free for:", v)
    if g["b"]:  # tpyc: ok
        print("free if: b nonempty")
    if g["a"]:
        print("free if: a nonempty")
    print("free in:", 2 in g["a"], 5 in g["a"], len(g))  # tpyc: ok
    g["a"][0] = 5  # tpyc: ok
    print("free setindex:", g["a"][0])
    g["c"] = [7, 8]  # tpyc: ok
    y: list[int32] = [9]
    g["d"] = y  # tpyc: ok
    g["e"] = new_list()  # tpyc: ok
    print("free setitem:", g["c"], g["d"], g["e"])
    s: DefaultDict[str, set[int32]] = DefaultDict(new_set)
    s["k"].add(3)
    t = s["k"]
    t.add(4)
    print("free set:", len(s["k"]), 4 in t)


def receivers() -> None:
    # A plain class: the reassigned alias re-points at the next row.
    r = Rows()
    x = r[0]  # tpyc: ok
    x.append(10)
    x = r[1]  # tpyc: ok
    x.append(20)
    print("rows:", r[0], r[1], len(r[0]))
    # A generic class instantiated at a container.
    b: Box[list[int32]] = Box()
    b.add([1])
    z = b[0]  # tpyc: ok
    z.append(2)
    print("box:", b[0], len(b[0]))
    # tplib.ArrayList of lists.
    al = ArrayList[list[int32], 4]()
    al.append([3])
    al[0] = [5]  # tpyc: ok
    print("arraylist setitem:", al[0])
    w = al[0]  # tpyc: ok
    w.append(4)
    al[0][0] = 30  # tpyc: ok
    print("arraylist:", al[0], len(al[0]))
    # A record result reassigned: the alias re-points, as for a builtin element.
    p = Pts()
    q = p[0]  # tpyc: ok
    q.x = 11
    q = p[1]  # tpyc: ok
    q.x = 22
    print("record reassign:", p[0].x, p[1].x)


def ro_reader(r: readonly[Rows]) -> int32:
    # A readonly receiver: the alias and the loop read a const element.
    x = r[0]  # tpyc: ok
    t = 0
    for v in r[1]:  # tpyc: ok
        t += v
    return len(x) + t


def ro_records(lst: readonly[ArrayList[Pt, 4]]) -> int32:
    # A readonly receiver's RECORD result reads as a const record.
    p = lst[0]  # tpyc: ok
    return p.x + lst[1].x  # tpyc: ok


class Grouper:
    rows: Rows

    def __init__(self) -> None:
        self.rows = Rows()

    # Method: the receiver is a field.
    def add(self, i: int32, v: int32) -> None:
        self.rows[i].append(v)  # tpyc: ok

    def size(self, i: int32) -> int32:
        return len(self.rows[i])  # tpyc: ok


class Holder[T: ValueType]:
    box: Box[list[T]]

    def __init__(self) -> None:
        self.box = Box()

    # Generic class method: the element is `list[T]`.
    def first(self, v: T) -> int32:
        self.box.add([v])
        row = self.box[0]  # tpyc: ok
        row.append(v)
        return len(self.box[0])  # tpyc: ok


def walk(b: Box[list[int32]]) -> Iterator[int32]:
    # Generator: iterating the element inside a resumable body.
    for v in b[0]:  # tpyc: ok
        yield v


def with_closure(r: Rows) -> int32:
    # Closure: the element read inside a nested function.
    def count(i: int32) -> int32:
        return len(r[i])  # tpyc: ok
    return count(0)


def main() -> None:
    free_function()
    receivers()
    gr = Grouper()
    gr.add(0, 5)
    gr.add(0, 6)
    print("method:", gr.size(0), gr.rows[0])
    h: Holder[int32] = Holder()
    print("generic method:", h.first(4), h.box[0])
    b: Box[list[int32]] = Box()
    b.add([6, 7])
    for v in walk(b):
        print("generator:", v)
    r = Rows()
    r[0].append(8)
    print("closure:", with_closure(r))
    print("readonly:", ro_reader(r))
    pa = ArrayList[Pt, 4]()
    pa.append(Pt(3))
    pa.append(Pt(4))
    print("readonly records:", ro_records(pa))


main()

