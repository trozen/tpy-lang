# Regression: `isinstance(e, Sub)` on `readonly[Optional[Polymorphic]]`
# used to fall through to holds_alternative on a non-variant pointer.
from typing import Optional
from tpy import readonly


def classify(e: readonly[Optional[BaseException]]) -> str:
    if e is None:
        return "<none>"
    if isinstance(e, ValueError):
        return "VE"
    if isinstance(e, (OSError, RuntimeError)):
        return "OS_OR_RT"
    return "OTHER"


def main() -> None:
    print(classify(None))
    print(classify(ValueError("v")))
    print(classify(OSError("o")))
    print(classify(RuntimeError("r")))
    print(classify(KeyError("k")))


main()
