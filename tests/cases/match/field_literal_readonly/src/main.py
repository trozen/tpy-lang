# Literal sub-patterns on a readonly-qualified field, plain and `as`-bound. A
# readonly view compares by value, so the qualifier must not reach the
# literal's kind check as an unrecognized wrapper.
from tpy import readonly


class R:
    s: readonly[str]

    def __init__(self, s: str) -> None:
        self.s = s


def describe(r: R) -> str:
    match r:
        case R(s="hi"):  # tpyc: ok
            return "hi"
        # The `as` spelling routes the same field type through the same check.
        case R(s="bye" as v):  # tpyc: ok
            return "bye:" + v
        case _:
            return "other"


def main() -> None:
    print(describe(R("hi")))
    print(describe(R("bye")))
    print(describe(R("yo")))


main()
