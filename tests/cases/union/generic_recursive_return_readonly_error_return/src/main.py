# @error_return on a readonly[Tree[T]] return: pre-fix functions.py:573
# tested wrapper-ness on the raw return_type (ReadonlyType, no
# needs_wrapper override -- False), so the val_or_ref wrap fired and
# generated std::expected<val_or_ref<Tree<T>>, E> while the body
# emitted a value-form Tree<T>. C++ rejected the mismatch.
from tpy import Int32, readonly, error_return, ReturnException


class E(Exception, ReturnException):
    pass


type Tree[T] = T | list[Tree[T]]


@error_return(E)
def f() -> readonly[Tree[Int32]]:
    return [1, 2]


def main() -> None:
    try:
        f()
        print("ok")
    except E:
        print("err")


main()
