# `Literal[str]`-annotated local rejects a non-literal-source RHS: sema cannot
# prove the runtime value is in the declared set, so the assignment is not
# allowed (would otherwise miscompile via Literal-specialized dispatch).
from typing import Literal


def get_mode() -> str:
    return "wb"


def main() -> None:
    m: Literal["r", "w"] = get_mode()  # tpyc: error(/Literal\["r", "w"\].*str/)
    print(m)


main()
