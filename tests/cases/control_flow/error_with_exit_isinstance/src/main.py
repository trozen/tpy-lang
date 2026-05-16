# v1.5 M1: class-based exception dispatch is deferred to M2.
# isinstance(exc_val, X) on Optional[BaseException] emits a targeted
# diagnostic pointing users at `if exc_val is not None:` for binary
# suppression.

from typing import Optional


class CM:
    def __enter__(self) -> int:
        return 1

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if isinstance(exc_val, ValueError):  # tpyc: error(/class-based exception dispatch is not yet supported/)
            return True
        return False


def main() -> None:
    pass


main()
