# A `nonlocal` in an arm that never runs still declares the name for the
# whole closure, so its `del` meets the deferred-return check as the live
# `nonlocal` does (exceptions/error_finally_closure_del_returned_local). The
# refusal is a restriction: BUGS.md#finally-closure-del-returned-nonlocal-rejects.
from tpy import Own


def f() -> Own[list[int]]:
    x: list[int] = [1, 2, 3]

    def drop() -> None:
        if False:
            nonlocal x
        x.append(4)
        del x  # tpyc: error(/cannot delete nonlocal 'x' here/)

    try:
        return x
    finally:
        drop()


print(f())
