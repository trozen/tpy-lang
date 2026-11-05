# A one-character str literal does not rank at a char overload: chd("a") over
# chd(x: char) / chd(x: int64) is refused, where a single char callee takes
# it (BUGS.md#dispatch-str-literal-to-char-refused).
from tpy import char, dispatch, int64


@dispatch
def chd(x: char) -> str:
    return "chd char " + str(x)


@dispatch
def chd(x: int64) -> str:
    return "chd int"


def main() -> None:
    print(chd("a"))  # tpyc: error(/No matching overload for 'chd' with argument types \(str\)/)


main()
