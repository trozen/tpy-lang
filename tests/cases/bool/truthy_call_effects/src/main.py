# A record with no __bool__/__len__ is always truthy, but the operand still has
# to be evaluated -- folding `if make():` to `true` would drop the call's side
# effects. The returned record is deliberately discarded; the observable is the
# global counter each call mutates. The value folds; the operand never does,
# whatever its shape -- its render can carry effects or a runtime check.
from tpy import Own


class Rec:
    v: int

    def __init__(self, v: int):
        self.v = v


calls = 0


def make() -> Own[Rec]:
    global calls
    calls += 1
    return Rec(1)


class Holder:
    r: Rec

    def __init__(self):
        self.r = Rec(0)

    @property
    def made(self) -> Own[Rec]:
        return make()


def yes() -> bool:
    return True


def main() -> None:
    if make():
        print("if:", calls)

    while make():
        break
    print("while:", calls)

    assert make()
    print("assert:", calls)

    if make() and yes():
        print("and:", calls)

    if yes() or make():
        print("or short-circuits:", calls)

    # A property getter runs user code behind field-access syntax, so the fold
    # has to keep it too.
    h = Holder()
    if h.made:
        print("property:", calls)

    # A name and a field read fold their VALUE the same way, and the snapshot
    # pins that they still evaluate.
    r = Rec(2)
    if r:
        print("name operand ok")
    if h.r:
        print("field operand ok")
    print("total:", calls)


main()
