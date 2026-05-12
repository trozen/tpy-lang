# Regression: ClassName.staticmethod() works cross-module when ClassName is
# imported via `from m import ClassName`. The static-method dispatcher used to
# only look up imported classes in the builtin registry, missing user modules.
from factory import Counter


def main() -> None:
    c = Counter.make(7)  # tpyc: ok
    print(c.value)
    z = Counter.zero()  # tpyc: ok
    print(z.value)


main()
