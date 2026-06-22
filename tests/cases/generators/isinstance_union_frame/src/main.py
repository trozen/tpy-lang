# isinstance-narrowing of a union-typed local in a generator frame: std::get /
# holds_alternative must unwrap the frame_slot<variant<...>>, not the slot wrapper.
from typing import Iterator
from tpy import Own


class Push:
    items: list[int]

    def __init__(self, items: Own[list[int]]) -> None:
        self.items = items


class Emit:
    label: str

    def __init__(self, label: str) -> None:
        self.label = label


def run() -> Iterator[int]:
    work: list[Push | Emit] = []
    work.append(Emit("ab"))
    work.append(Push([1, 2, 3]))
    while len(work) > 0:
        t = work.pop()
        if isinstance(t, Push):  # tpyc: ok
            # mutate through the narrowed alias; observing it proves a borrow
            # into the frame variant, not a copy.
            t.items.append(99)
            total = 0
            for n in t.items:
                total += n
            yield total
        else:
            yield len(t.label)


def first_value(t: Push | Emit) -> Iterator[int]:
    # assert-narrowing (the persistent extraction path) of a frame-resident union.
    assert isinstance(t, Push)  # tpyc: ok
    yield t.items[0]


def plain(t: Push | Emit) -> int:
    # Inverse: the same narrowing in a non-generator must keep working.
    if isinstance(t, Push):
        return len(t.items)
    return -1


def main() -> None:
    for v in run():
        print(v)
    for v in first_value(Push([7, 8])):
        print("first", v)
    print("plain", plain(Push([1, 2, 3, 4])))
    print("plain", plain(Emit("z")))


main()
