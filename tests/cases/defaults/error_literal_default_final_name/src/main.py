# A named Final[str] default at a Literal parameter is refused: its type is
# str, not one of the Literal's values, and the diagnostic names them.
from typing import Final, Literal

M: Final[str] = "z"


def f(m: Literal["a", "b"] = M) -> str:  # tpyc: error(/expected Literal\["a", "b"\], got StrView; pass one of "a", "b"/)
    return "f " + m


def main() -> None:
    print(f())


main()
