# A call macro that reads its argument's literal value must reject a
# non-literal argument with a clear diagnostic, not silently substitute a
# default. Passing a variable (not a literal) is a hard compile error.
from polymac import poly


def main() -> None:
    name = "world"
    s = poly(name)  # tpyc: error(/literal/)
    print(s)


main()
