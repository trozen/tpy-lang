# Whole aliases and alias chains share the records owned by a local tuple.
# Mutations through either name must be visible through the other; @nocopy forbids copies.
from tpy import int32, nocopy


@nocopy
class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value


def free(flag: bool) -> int32:
    pair = (Cell(1), 7, Cell(2))
    # Neither binding may copy the inline records, including through a chain.
    saved = pair  # tpyc: ok
    chain = saved  # tpyc: ok
    if flag:
        chain[0].value = 9  # tpyc: ok
    pair[2].value = 8
    # The two paths observe writes through the alias and the original name.
    return pair[0].value if flag else saved[2].value


def scalar_members() -> int32:
    pair = (Cell(1), 7, True)
    saved = pair  # tpyc: ok
    # Aliasing an owning tuple must retain its scalar members too.
    return saved[1] if saved[2] else 0


def repeated(again: bool) -> int32:
    pair = (Cell(1),)
    saved = pair  # tpyc: ok
    while True:
        # The outer alias keeps referring to the same backing across iterations.
        if again:
            saved[0].value = 9  # tpyc: ok
            again = False
            continue
        break
    return pair[0].value


class Runner:
    result: int32

    def __init__(self):
        self.result = 0
        pair = (Cell(1),)
        # Constructor-tail aliases share the singleton's record.
        saved = pair  # tpyc: ok
        saved[0].value = 12  # tpyc: ok
        self.result = pair[0].value

    def method(self) -> int32:
        pair = (Cell(2), 7)
        # A method's alias chain sees a later write through the original name.
        saved = pair  # tpyc: ok
        chain = saved  # tpyc: ok
        pair[0].value = 13
        return chain[0].value


def main() -> None:
    print("free", free(True), free(False))
    print("scalars", scalar_members())
    print("loop", repeated(True), repeated(False))
    runner = Runner()
    print("constructor", runner.result)
    print("method", runner.method())


main()
