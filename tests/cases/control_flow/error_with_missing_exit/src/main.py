# Error: with statement on type missing __exit__
from typing import Self


class NoExit:
    def __enter__(self) -> Self:
        return self


def main() -> None:
    with NoExit() as n:  # tpyc: error(/missing __exit__/)
        pass

main()
