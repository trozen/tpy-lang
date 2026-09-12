# An import alias binds `cls` too.
from tpy import int32


class P:
    @classmethod
    def f(cls) -> int32:
        import os as cls  # tpyc: error(/Cannot rebind 'cls'/)
        return 1


def main() -> None:
    print(P.f())


main()
