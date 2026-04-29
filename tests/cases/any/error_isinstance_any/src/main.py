# Any is not a runtime class -- isinstance(x, Any) is rejected
# (matches Python's TypeError at runtime).

from typing import Any


def main() -> None:
    x: Any = 1
    if isinstance(x, Any):  # tpyc: error(/isinstance.+ cannot be Any/)
        print("never")


main()
