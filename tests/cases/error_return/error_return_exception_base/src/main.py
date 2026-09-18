# A return-only exception is a plain value: deriving it from a concrete thrown
# exception would bring the Throwable hierarchy back, so the base is refused.
from tpy import int32, error_return, ReturnException


# the subject: only `Exception` is accepted as the class parent
class Missing(ValueError, ReturnException):  # tpyc: error(/must derive directly from Exception/)
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
    except Missing:
        print("missing")


main()
