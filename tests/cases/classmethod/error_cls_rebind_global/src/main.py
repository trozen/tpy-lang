# `global cls` would rebind the alias (CPython rejects it outright as
# "name 'cls' is parameter and global").
from tpy import Int32

counter: Int32 = 0


class P:
    @classmethod
    def f(cls) -> Int32:
        global cls  # tpyc: error(/Cannot rebind 'cls'/)
        return 1


def main() -> None:
    print(P.f())


main()
