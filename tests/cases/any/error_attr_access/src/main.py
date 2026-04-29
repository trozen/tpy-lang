# Attribute access on raw Any is rejected -- the compiler doesn't know
# what type has the attribute. User must narrow first.

from typing import Any


def main() -> None:
    a: Any = "hello"
    print(a.upper)  # tpyc: error(/[Cc]annot access field.*on type Any/)


main()
