# Caller has @error_return(SomeOtherType): AttributeError from `obj.foo` cannot
# auto-propagate as a different return type. Sema rejects with try/except hint.
from tpy import error_return, ReturnException


class StopIt(Exception, ReturnException):
    pass


class Bag:
    def __getattr__(self, name: str) -> str:
        raise AttributeError(name)


@error_return(StopIt)
def fetch(b: Bag) -> str:
    return b.host  # tpyc: error(/may return 'AttributeError' which must be handled/)


def main() -> None:
    pass


main()
