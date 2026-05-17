# Pins the boundary of the v1.5 M1 diagnostic: `isinstance(exc_val, X)` on
# Optional[BaseException] emits the targeted message *regardless of context*,
# not only inside __exit__ bodies. The error_with_exit_isinstance case covers
# the in-__exit__ form; this case covers a plain function with an
# Optional[BaseException] parameter to pin the broader scope.

from typing import Optional


def classify(e: Optional[BaseException]) -> None:
    if isinstance(e, ValueError):  # tpyc: error(/class-based exception dispatch on Optional\[BaseException\] is not yet supported/)
        print("value")


def main() -> None:
    pass


main()
