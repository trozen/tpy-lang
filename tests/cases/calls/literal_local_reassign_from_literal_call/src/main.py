# `Literal[str]`-annotated local reassigned from a Literal-returning function:
# both initial string-literal RHS and the Literal-call source are view-safe,
# so the local keeps view storage across the reassignment.
from typing import Literal


def get_r() -> Literal["r"]:
    return "r"


def main() -> None:
    m: Literal["r", "w", "rb", "wb"] = "r"
    print(m)
    m = get_r()
    print(m)


main()
