# A comprehension passed to a user-record method's read-only container slot:
# the method arg loop renders the statement-expression INLINE, where the same
# argument at a free-function slot hoists a named temp first.
from tpy import Own


class Sink:
    total: float

    def __init__(self) -> None:
        self.total = 0.0

    def saverow(self, row: list[float]) -> Own[str]:
        out = ""
        for v in row:
            out += f"{v} "
        return out

    def count(self, seen: set[int]) -> int:
        return len(seen)

    def index(self, m: dict[int, float]) -> int:
        return len(m)


def take(row: list[float]) -> int:
    return len(row)


def main() -> None:
    s = Sink()
    a = [1.0, 2.0, 3.0]
    # The subject: list / set / dict comprehensions at a method's const slot.
    print(s.saverow([2.0 * a[i] for i in range(3)]))  # tpyc: ok
    print(s.count({i * 2 for i in range(4)}))  # tpyc: ok
    print(s.index({i: a[i] for i in range(3)}))  # tpyc: ok
    # The free-call sibling, which hoists its own temp instead.
    print(take([2.0 * a[i] for i in range(3)]))  # tpyc: ok


main()
