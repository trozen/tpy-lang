# An unhandled `@error_return` operator dunder is refused as its method
# twin is; the handled form is BUGS.md#error-return-operator-bridge-unlocated.
from tpy import int32, error_return, ReturnException


class Bad(Exception, ReturnException):
    pass


class A:
    a: int32

    def __init__(self, a: int32) -> None:
        self.a = a

    @error_return(Bad)
    def __add__(self, o: int32) -> int32:
        if o < 0:
            raise Bad
        return self.a + o


def use_add(r: A) -> int32:
    return r + 1  # tpyc: error(/may return 'Bad' which must be handled/)


def main() -> None:
    print(use_add(A(1)))


main()
