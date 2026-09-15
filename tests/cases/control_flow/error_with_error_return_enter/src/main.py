# A `with` whose `__enter__` is @error_return. The header calls the dunder
# implicitly, so nothing credits the enclosing try/except with handling its
# error; before the reject the target bound the raw error result and g++
# failed with no TPy location. See BUGS.md#with-header-error-return-dunder.
# The subject is the OVERLOADED spelling with the @error_return variant
# SECOND: the header resolves nothing (a dunder with no arguments beside
# `self`), so the reject has to scan every variant rather than the one the
# rest of the function reads. The everyday single-variant spelling is pinned
# by `error_with_error_return_enter_single` beside it (an error_ case stops at
# the first error, so neither can hold both).
from tpy import ReturnException, dispatch, error_return, int32, readonly


class Gone(Exception, ReturnException):
    pass


class Res:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    @dispatch
    @readonly
    def __enter__(self) -> int32:
        return 7

    @dispatch
    @error_return(Gone)
    def __enter__(self) -> int32:
        return 8

    def __exit__(self, et, exc_val, etb) -> None:
        self.n = 1


def main() -> None:
    r = Res()
    try:
        # the subject: a variant of the manager's enter is @error_return
        with r as v:  # tpyc: error(/no way to handle/)
            print(v)
    except Gone:
        pass


main()
