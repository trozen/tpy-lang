# The everyday spelling of BUGS.md#with-header-error-return-dunder: ONE
# `__enter__`, declared @error_return. The `with` header calls the dunder
# implicitly, so nothing credits the enclosing try/except with handling its
# error. An error_ case holds one error, so the OVERLOADED spelling (where the
# reject has to scan every variant rather than the resolved one) is pinned by
# `error_with_error_return_enter` beside it.
from tpy import ReturnException, error_return, int32


class Gone(Exception, ReturnException):
    pass


class Res:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    @error_return(Gone)
    def __enter__(self) -> int32:
        return 8

    def __exit__(self, et, exc_val, etb) -> None:
        self.n = 1


def main() -> None:
    r = Res()
    try:
        # the subject: the manager's only enter is @error_return
        with r as v:  # tpyc: error(/no way to handle/)
            print(v)
    except Gone:
        pass


main()
