# A nested def declaring a param DEFAULT. The default is dropped with a warning
# naming it: the lambda takes the plain param list, and a call that relies on the
# default (`scale()`) is then rejected with "'scale' expects 1 argument(s), got
# 0" -- a defect, not the design (BUGS.md#nested-def-default-ignored). This case
# pins the warning plus the calls that pass every argument.
from tpy import int32


def main() -> None:
    def scale(x: int32 = 1) -> int32:  # tpyc: warning(/default value for parameter 'x'.*is ignored/)
        return x * 3

    def label(prefix: str, n: int32 = 0) -> str:  # tpyc: warning(/default value for parameter 'n'.*is ignored/)
        return prefix + str(n)

    print(scale(4))
    print(label("n=", 7))


main()
