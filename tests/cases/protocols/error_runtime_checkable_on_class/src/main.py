# @runtime_checkable applies only to protocols; CPython raises TypeError on
# a plain class too.
from typing import runtime_checkable


@runtime_checkable  # tpyc: error(/applies only to protocols/)
class Plain:
    def __init__(self) -> None:
        pass


def main() -> None:
    print(Plain())


main()
