# v1.5 M2: isinstance check type on Optional[BaseException] must be a
# subclass of BaseException. Using a non-exception class as the check type
# is rejected with a clear sema diagnostic. Replaces the negative coverage
# the old M1 stopgap tests provided before they were converted to positive
# M2 tests.

from typing import Optional


class NotAnException:
    def __init__(self) -> None:
        pass


def classify(e: Optional[BaseException]) -> bool:
    return isinstance(e, NotAnException)  # tpyc: error(/not subclasses of 'BaseException'/)


def main() -> None:
    pass


main()
