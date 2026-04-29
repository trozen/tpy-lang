# for x in any_var is rejected -- narrow first.

from typing import Any


def main() -> None:
    a: Any = [1, 2, 3]
    for x in a:  # tpyc: error(/[Cc]annot iterate over type Any/)
        print(x)


main()
