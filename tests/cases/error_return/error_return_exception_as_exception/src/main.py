# A return-only exception is a plain value, not an Exception: passing the bound
# error where an Exception is expected is refused with a pointed message.
from tpy import int32, error_return, ReturnException


class Missing(Exception, ReturnException):
    pass


@error_return(Missing)
def find(k: int32) -> int32:
    if k == 1:
        return 1
    raise Missing


def log(e: Exception) -> None:
    print(str(e))


def main() -> None:
    try:
        v = find(2)
        print(v)
    except Missing as e:
        # the subject: CPython accepts this, TPy names why it cannot
        log(e)  # tpyc: error(/return-only exception.*cannot be used as 'Exception'/)


main()
