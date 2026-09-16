# A spawn task whose run() hands back a REFERENCE type: the thread result is
# owned (it crosses the thread boundary), so JoinHandle.join() returns Own[R]
# and a list / @nocopy-record result moves out of the future intact. Sections:
# a list[str] result mutated after the join, a @nocopy record result (a silent
# copy would be a compile error) mutated after the join, handles minted by a
# comprehension, join inside a method, and a value-typed result as the
# regression guard for the shape that worked.
from tpy import Own, int32, nocopy
from tpy.thread import spawn


@nocopy
class Rows:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def run(self) -> Own[list[str]]:
        out: list[str] = []
        for i in range(self.n):
            out.append("r" + str(i))
        return out


@nocopy
class Tally:
    total: int32
    tags: list[str]

    def __init__(self, total: int32, tags: Own[list[str]]) -> None:
        self.total = total
        self.tags = tags


@nocopy
class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def run(self) -> Own[Tally]:
        tags: list[str] = []
        total = 0
        for i in range(self.n):
            tags.append("t" + str(i))
            total += i
        return Tally(total, tags)


@nocopy
class Summer:
    data: list[int32]

    def __init__(self, data: Own[list[int32]]) -> None:
        self.data = data

    def run(self) -> int32:
        total = 0
        for v in self.data:
            total += v
        return total


class Driver:
    label: str

    def __init__(self, label: str) -> None:
        self.label = label

    def collect(self, n: int32) -> None:
        h = spawn(Rows(n))
        got = h.join()  # tpyc: ok
        got.append(self.label)
        print("method:", " ".join(got))


def main() -> None:
    # list result: joined, then mutated, and the mutation is observed.
    h = spawn(Rows(3))
    rows = h.join()  # tpyc: ok
    rows.append("extra")
    print("list:", " ".join(rows))

    # @nocopy record result: a silent copy anywhere on this path would be a
    # compile error, so the record really moved out of the thread's future.
    t = spawn(Counter(4)).join()  # tpyc: ok
    t.total += 100
    t.tags.append("late")
    print("record:", t.total, " ".join(t.tags))

    # value-typed result: the shape that already worked, kept as a guard.
    s = spawn(Summer([1, 2, 3])).join()  # tpyc: ok
    print("value:", s)

    # comprehension position: the handles are minted by a comprehension, and
    # joined by a second one -- join() yields an `Own[list[str]]`, which the
    # container element slot takes as the storage value it already is.
    hs = [spawn(Rows(t)) for t in range(3)]  # tpyc: ok
    joined = [h.join() for h in hs]  # tpyc: ok
    joined[2].append("post")
    # the element is bound first: a container subscript at a method arg still
    # rejects (BUGS.md#container-subscript-into-method-arg). The binding must
    # ALIAS the element, so the section mutates the element after taking it and
    # reads the mutation back THROUGH the binding -- a read-only bind would
    # match CPython's output even if TPy had copied.
    last = joined[2]
    joined[2].append("aliased")
    print("comprehension:", len(joined), " ".join(last))

    Driver("end").collect(2)


main()
