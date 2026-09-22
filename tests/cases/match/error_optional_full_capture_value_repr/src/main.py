# A whole-subject capture of a VALUE-repr non-scalar Optional (`Optional[str]`)
# has no local render for the bound Optional, so it still rejects where the
# pointer-repr record twin now binds. Workaround: split the arm into
# `case None:` and a value arm (BUGS.md#match-optional-value-repr-full-capture).
from typing import Optional


def pick(s: Optional[str]) -> str:
    match s:  # tpyc: error(/not yet supported/)
        case "a":
            return "A"
        case v:
            if v is None:
                return "none"
            return v


def main() -> None:
    print(pick("b"))


main()
