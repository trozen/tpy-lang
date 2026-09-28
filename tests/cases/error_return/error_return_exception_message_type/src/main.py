# str(e) of a return-only exception renders its declared `message` field, so
# that field must be a str: any other type would print nothing where CPython
# prints the value.
from tpy import int32, error_return, ReturnException


class Failed(Exception, ReturnException):
    # the subject: `message` re-declared with a type other than str
    message: int32  # tpyc: error(/Field 'message' of return-only exception 'Failed' must be 'str'.*got 'int32'/)

    def __init__(self, message: int32) -> None:
        super().__init__()
        self.message = message


@error_return(Failed)
def run() -> int32:
    raise Failed(42)


def main() -> None:
    try:
        v = run()
        print(v)
    except Failed as e:
        print(str(e))


main()
