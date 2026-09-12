# Imported generic union alias used cross-module. Regression test for
# the dangling `using Local = Src::Original;` emission bug that fired
# before the codegen skip-for-generic-aliases guard at the imported-
# alias loop.
from tpy import int32
from lib import Either, A, B


def describe(e: Either[int32]) -> str:
    if isinstance(e, A):
        return "A"
    return "B"


def main() -> None:
    print(describe(A(int32(1))))
    print(describe(B(int32(2))))


main()
