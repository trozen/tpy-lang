# Return-tier sibling of exceptions/raise_optional_arg: raise E(None) in an
# @error_return fn lowers through make_unexpected with the same None->nullopt coercion.
from tpy import Int32, error_return, ReturnException


class Failed(Exception, ReturnException):
    code: Int32 | None

    def __init__(self, code: Int32 | None) -> None:
        super().__init__("failed")
        self.code = code


@error_return(Failed)
def run(ok: bool) -> Int32:
    if ok:
        return 1
    raise Failed(None)


def main() -> None:
    try:
        print(run(True))
    except Failed:
        print("failed")
    try:
        print(run(False))
    except Failed as e:
        print("code none" if e.code is None else "code set")


main()
