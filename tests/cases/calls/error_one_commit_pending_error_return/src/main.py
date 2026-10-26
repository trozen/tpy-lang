# An `@error_return` call must be handled (LANGUAGE_FEATURES "Error Handling"),
# also a method on a receiver whose type argument is still open.
from tpy import int32, error_return, ReturnException


class Bad(Exception, ReturnException):
    pass


class DBag[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def put(self, x: T) -> None:
        self.items.append(x)

    @error_return(Bad)
    def check(self, n: int32) -> int32:
        if n < 0:
            raise Bad
        return n


def main() -> None:
    b = DBag()
    r = b.check(1)  # tpyc: error(/call to 'check' may return 'Bad' which must be handled/)
    b.put(r)
    print(r, b.items)


main()
