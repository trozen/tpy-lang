# `Literal[str]`-annotated local initialized from a Literal-returning function:
# Literal returns emit `std::string_view` storage (their values are static-
# lifetime string literals), so the local also keeps view storage end-to-end --
# no heap allocation. Regression guard for the view-safety inference path.
from typing import Literal


def get_r() -> Literal["r"]:
    return "r"


def main() -> None:
    m: Literal["r", "w", "rb", "wb"] = get_r()
    print(m)


main()
