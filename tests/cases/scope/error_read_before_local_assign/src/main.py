# A name a function binds anywhere (here a later `for` target) is local to the
# whole function, so a call through it before the binding is CPython's
# UnboundLocalError -- it never reaches the module function of that name.
from tpy import int32


def helper() -> int32:
    return 1


def main() -> None:
    # The callee read the later `for` makes local.
    print(helper())  # tpyc: error(/'helper' is read before it is bound on line 14: a name bound anywhere in a function is local to the whole function, so this read does not see the module's 'helper'; rename one of them/)
    for helper in range(2):
        pass


main()
