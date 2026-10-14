# A dotted except clause naming a member that exists but is no class says
# so, rather than that the member is missing. Rule: LANGUAGE_FEATURES.md
# "Error Handling" (an except clause names an exception class; CPython
# raises TypeError only when an exception reaches the clause).
from typing import Final
from tpy import int32


class Outer:
    LIMIT: Final[int32] = 5


def main() -> None:
    try:
        print("body")
    except Outer.LIMIT:  # tpyc: error(/'Outer.LIMIT' is not an exception class/)
        print("caught")


main()
