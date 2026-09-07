# An `Own[str]` argument in a WHILE CONDITION hoists its typed copy temp into the
# restructured loop head; the callee's own `Own[str]` param takes the same
# copy-plus-move temp at the element slot (the copy before the move is
# BUGS.md#own-str-slot-temp-copies-owned-source).
from tpy import Own


class Sink:
    kept: list[str]

    def __init__(self) -> None:
        self.kept = []

    def check(self, v: Own[str]) -> bool:
        self.kept.append(v)  # Own[str] param at an Own[str] element slot
        return len(self.kept) < 2


class Row:
    label: str

    def __init__(self, label: str) -> None:
        self.label = label


def drain(s: Sink, r: Row) -> None:
    while s.check(r.label):  # the temp lands in the loop head
        break


def main() -> None:
    s = Sink()
    drain(s, Row("a"))
    print(len(s.kept), s.kept[0])


main()
