# Reassigning a `Literal[str]`-annotated local from a non-literal source is
# rejected for the same reason as the init case.
from typing import Literal


def get_mode() -> str:
    return "wb"


def main() -> None:
    m: Literal["r", "w"] = "r"
    m = get_mode()  # tpyc: error(/Literal\["r", "w"\].*str/)
    print(m)


main()
