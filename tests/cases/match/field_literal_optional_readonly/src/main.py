# A literal sub-pattern on a field whose type composes BOTH qualifier layers.
# `Optional[readonly[str]]` is the nesting that forces the descent to the
# compared type to repeat: peeling once leaves the readonly view in place, and
# the literal's kind check then rejects a field that is plainly a str.
from typing import Optional

from tpy import readonly


class R:
    inner: Optional[readonly[str]]

    def __init__(self) -> None:
        self.inner = None


def describe(r: R) -> str:
    match r:
        case R(inner="hi"):  # tpyc: ok
            return "hi"
        # The `as` spelling routes the same field type through the same check.
        case R(inner="bye" as v):  # tpyc: ok
            return "bye" if v is not None else "unreachable"
        case _:
            return "other"


def main() -> None:
    a = R()
    a.inner = "hi"
    print(describe(a))
    b = R()
    b.inner = "bye"
    print(describe(b))
    print(describe(R()))


main()
