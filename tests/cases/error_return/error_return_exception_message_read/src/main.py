# A return-only exception has no inherited `message`: reading it on a class that
# does not declare one is refused (a thrown Exception always has it).
from tpy import int32, error_return, ReturnException


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
        # the subject: `message` is not a field of this class
        print(e.message)  # tpyc: error(/'Missing' has no field 'message': a return-only exception.*declare 'message'/)


main()
