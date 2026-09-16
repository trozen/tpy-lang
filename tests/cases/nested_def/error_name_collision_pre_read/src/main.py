# A nested def that shadows a module function, read EARLIER in the enclosing
# body than the `def`. The `def` makes the name a local of the whole body, so
# CPython raises UnboundLocalError; sema now reports the read rather than
# resolving it through the registry to the module `helper`.
from tpy import int32


def helper(x: int32) -> int32:
    return x + 1


def main() -> None:
    # the read cannot reach the module function: `helper` is main's local here
    print("before:", helper(1))  # tpyc: error(/is read before the nested function 'helper'/)

    def helper(x: int32) -> int32:
        return x + 100

    print("after:", helper(1))


main()
