# A nested def that shadows a module function and binds the same name with
# `except ... as`. That binding is scoped to the handler, so the read after it
# is still the nested def's own name (CPython raises UnboundLocalError there --
# the handler binding is deleted at the handler's end) rather than the module
# function.
from tpy import int32


def helper(x: int32) -> int32:
    return 4012


def main() -> None:
    def helper(x: int32) -> int32:
        if x > 100:
            return x
        try:
            raise ValueError("v")
        except ValueError as helper:
            pass
        # the handler binding is gone here, so this read is the nested def
        return helper(x + 200)  # tpyc: error(/Recursive nested functions are not supported/)

    print(helper(1))


main()
