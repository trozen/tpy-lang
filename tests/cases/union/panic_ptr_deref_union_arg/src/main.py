# A null Ptr passed at a value-union parameter is dereferenced through the
# checked deref, as at a plain parameter of the pointee's type.
from tpy import Ptr


def f(x: bool | str) -> str:
    return "union"


def main() -> None:
    p: Ptr[bool] = Ptr[bool]()
    # The pointee converts into the `bool` member: a null pointer panics.
    print(f(p))


main()
