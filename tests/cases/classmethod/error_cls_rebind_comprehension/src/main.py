# A comprehension target named `cls` is rejected as a rebind: its binding is
# not visible to the name resolution that sees `cls` as the class, so allowing
# it would report a misleading error at the read instead.
from tpy import int32


class P:
    @classmethod
    def f(cls) -> int32:
        xs = [cls for cls in range(3)]  # tpyc: error(/Cannot rebind 'cls'/)
        return len(xs)


def main() -> None:
    print(P.f())


main()
