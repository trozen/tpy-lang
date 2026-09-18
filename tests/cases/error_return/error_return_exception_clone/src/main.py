# A return-only exception is never thrown, so it has none of the Throwable
# members a thrown exception carries: clone() is refused by name.
from tpy import int32, error_return, ReturnException
from tplib import Box


class Missing(Exception, ReturnException):
    pass


@error_return(Missing)
def find(k: int32) -> int32:
    if k == 1:
        return 1
    raise Missing


def main() -> None:
    try:
        v = find(2)
        print(v)
    except Missing as e:
        # the subject: storing the error polymorphically needs a Throwable
        b = Box(e.clone())  # tpyc: error(/return-only exception.*no 'clone\(\)'/)


main()
