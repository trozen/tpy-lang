# `qb`'s own E stays bound: CPython's star import copies no E here, and TPy
# keeps the module's own definition over a star copy.
def E() -> int:
    return 1


from qa import *  # noqa: E402  # tpyc: ok


def main() -> None:
    print("own above:", E())
