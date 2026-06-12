# An @error_return call inside a nested def must not inherit the enclosing
# function's try/except context: the nested def has no handler, so the
# must-handle check rejects (the goto target would be outside the lambda).
from tpy import error_return, ReturnException


class NotFound(Exception, ReturnException):
    pass


@error_return(NotFound)
def find(ok: bool) -> int:
    if not ok:
        raise NotFound
    return 1


def main() -> None:
    try:
        def g() -> int:
            return find(False)  # tpyc: error(/must be handled/)
        print(g())
    except NotFound:
        print("caught")


main()
