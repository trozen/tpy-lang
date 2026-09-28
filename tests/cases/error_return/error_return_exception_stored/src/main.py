# A caught return-only exception cannot yet be kept as an owned value: the
# diagnostic says so, instead of recommending the Box / clone() remedy a thrown
# exception gets (a return exception has neither).
from tpy import int32, error_return, ReturnException


class Bad(Exception, ReturnException):
    code: int32

    def __init__(self, code: int32) -> None:
        super().__init__()
        self.code = code


@error_return(Bad)
def run(ok: bool) -> int32:
    if ok:
        return 1
    raise Bad(4)


def main() -> None:
    last = Bad(0)
    try:
        v = run(False)
        print(v)
    except Bad as e:
        # the subject: CPython simply rebinds the name
        last = e  # tpyc: error(/cannot store the return-only exception 'Bad' as an owned value.*copy the fields/)
    print(last.code)


main()
