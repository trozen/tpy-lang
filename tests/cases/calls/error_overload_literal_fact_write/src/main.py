# A short literal-only @overload stub lowers, but a body that REASSIGNS a
# fact-carrying param must keep rejecting: the injected fact map is frozen while
# the literal value dies at the write, so the fold has no flow-sensitive form.
from typing import overload, Literal
from tpy import Int32


@overload
def norm(m: Literal["r", "w"]) -> Int32: ...  # tpyc: ok

@overload
def norm(m: Literal["x", "y"]) -> Int32: ...  # tpyc: ok

def norm(m: str) -> Int32:  # tpyc: error(/sig.overload_set.literal_fact_write/)
    m = "z"
    if m == "z":
        return 1
    return 2


def main() -> None:
    print(norm("r"), norm("x"))


main()
