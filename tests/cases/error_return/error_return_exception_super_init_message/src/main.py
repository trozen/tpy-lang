# A return-only exception has no Exception(message) constructor behind super():
# passing the message up is refused, and the diagnostic names the declared-field
# spelling. (A bare `super().__init__()` stays legal.)
from tpy import int32, error_return, ReturnException


class Failed(Exception, ReturnException):
    code: int32

    def __init__(self, code: int32) -> None:
        # the subject: the idiomatic CPython way to set an exception's message
        super().__init__("failed")  # tpyc: error(/return-only exception.*no Exception\(message\) constructor/)
        self.code = code


@error_return(Failed)
def run(ok: bool) -> int32:
    if ok:
        return 1
    raise Failed(7)


def main() -> None:
    try:
        v = run(False)
        print(v)
    except Failed as e:
        print(e.code)


main()
