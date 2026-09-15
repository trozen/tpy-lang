# A nested def that shadows a module function and binds the same name with
# `except ... as`. That binding is scoped to the handler, so it does not make
# the name the nested def's own; the read after the handler still resolves to
# the module function where CPython raises UnboundLocalError (the handler
# binding is a function local, deleted at the handler's end)
# (BUGS.md#nested-def-shadow-resolves-to-shadowed-callable).
from tpy import int32


def helper(x: int32) -> int32:
    return 4012


def main() -> None:
    # the `helper(x + 200)` read is OUTSIDE the handler; the reject is on the def
    def helper(x: int32) -> int32:  # tpyc: error(/nesteddef\.name_collision/)
        if x > 100:
            return x
        try:
            raise ValueError("v")
        except ValueError as helper:
            pass
        return helper(x + 200)

    print(helper(1))


main()
