# A return-only exception carries only the fields it declares: a `pass` class
# has no inherited Exception(message) constructor, so raising it with a message
# is refused and the diagnostic names the fix -- the one the expression
# Missing(message) gives.
from tpy import int32, error_return, ReturnException


class Missing(Exception, ReturnException):
    pass


@error_return(Missing)
def find(k: int32) -> int32:
    if k == 1:
        return 1
    # the subject: CPython accepts this through the inherited constructor
    raise Missing("no such key")  # tpyc: error(/'Missing' has no __init__: a return-only exception.*add 'message: str'/)


def main() -> None:
    try:
        v = find(2)
        print(v)
    except Missing:
        print("missing")


main()
