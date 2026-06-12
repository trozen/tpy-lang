# A plain `Foo | None` local is the owned-nullable spelling (no `Own`): it owns
# the value the owning call produced, and a write through the narrowed local is
# observable.
from tpy import Own


class Box:
    val: int

    def __init__(self, v: int):
        self.val = v


def make_box(v: int) -> Own[Box]:
    return Box(v)


def main() -> None:
    t: Box | None = make_box(5)  # tpyc: ok
    if t is not None:
        t.val += 1
        print(t.val)


main()
