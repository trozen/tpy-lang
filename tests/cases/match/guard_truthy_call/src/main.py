# A match case guard is a boolean context too: an always-truthy record-returning
# call in the guard must still be evaluated, not folded away.
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


def classify(x: int) -> str:
    match x:
        case 1 if make():
            return "one"
        case _:
            return "other"


def main() -> None:
    print(classify(1), calls)
    print(classify(2), calls)


main()
