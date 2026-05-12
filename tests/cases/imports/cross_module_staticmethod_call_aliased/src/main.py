# Regression: ClassName.staticmethod() works cross-module when ClassName is
# imported under an alias via `from m import ClassName as Alias`. The aliased
# binding should route to the same record info as the un-aliased form.
from factory import Counter as Ctr


def main() -> None:
    a = Ctr.make(99)  # tpyc: ok
    print(a.value)


main()
