# A generator bound in a loop over an element of a call's owned list that a
# walrus names, and read after the loop, is refused as the plain temporary is
# (generators/error_generator_temp_element_after_loop) -- conservatively: the
# frame's operand is not traced through the walrus to `t`
# (BUGS.md#loop-frame-check-precision), so it must not be admitted as
# borrowing nothing that can end.
from typing import Iterator

from tpy import Own, StrView


def names() -> Own[list[str]]:
    return ["temp-elem-" + str(len("ab")) * 30]


def gen(s: StrView) -> Iterator[StrView]:
    yield s


def outer() -> None:
    for i in range(3):
        g = gen((t := names())[0])
    # The subject: `g` is read after the loop that binds it.
    for x in g:  # tpyc: error(/cannot use 'g' after the loop it is bound in: what it borrows cannot be traced/)
        print(x)


outer()
