# An omitted typing.overload argument whose default is a one-character str
# literal is refused at the char copy, as the written c("q") is
# (BUGS.md#dispatch-str-literal-to-char-refused; the Final[char] default
# that runs: calls/overload_as_dispatch "char").
from typing import overload
from tpy import char, int32


@overload
def c(x: char = "q") -> str: ...
@overload
def c(x: char, y: int32) -> str: ...
def c(x: char = "q", y: int32 = 0) -> str:
    return "c " + str(x)


def main() -> None:
    # the call omits `x`, whose "q" default no char copy ranks
    print(c())  # tpyc: error(/No matching overload for 'c' with argument types \(\)/)


main()
