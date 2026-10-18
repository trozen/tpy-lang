# A generator bound in a loop over an element of a temporary (a call's
# owned list) and read after the loop is refused: the frame keeps a view
# into storage that dies with the binding statement.
from typing import Iterator

from tpy import Own, StrView


def names() -> Own[list[str]]:
    return ["temp-elem-" + str(len("ab")) * 30]


def gen(s: StrView) -> Iterator[StrView]:
    yield s


def outer() -> None:
    for i in range(3):
        g = gen(names()[0])
    # The subject: `g` is read after the loop that binds it.
    for x in g:  # tpyc: error(/cannot use 'g' after the loop it is bound in: what it borrows cannot be traced/)
        print(x)


outer()
