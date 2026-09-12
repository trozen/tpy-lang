# Returning Optional[wrapper] by value requires Own[] because the
# default Optional[Wrapper] representation is `Tree<T>*` (pointer-repr),
# which would dangle when bound to a function-result rvalue. The Own
# wrapper triggers move/by-value return semantics for the whole
# Optional. This documents the working pattern; the parallel error
# case (`error_generic_recursive_optional_return_dangle`) shows the
# diagnostic users get without Own.
from typing import Optional
from tpy import int32, Own

type Tree[T] = T | list[Tree[T]]


def build() -> Own[Tree[int32]]:
    return 7


def make_some() -> Own[Optional[Tree[int32]]]:
    return build()


def make_none() -> Own[Optional[Tree[int32]]]:
    return None


def main() -> None:
    s = make_some()
    n = make_none()
    print(s is not None)
    print(n is None)


main()
