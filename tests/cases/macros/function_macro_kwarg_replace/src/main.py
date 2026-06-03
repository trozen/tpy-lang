# A function macro replaces string-literal bool kwargs (`flag="true"`) with
# bool literals via replace_expr. The kwarg expr lives in a dict field, so this
# only compiles if replace_expr descends into dict-valued fields. Without the
# replacement, `flag="true"` would be a str passed to a bool param (error).
from kwargmod import kwarg_bool
from tpy import Int32


def takes(flag: bool) -> Int32:
    return 1 if flag else 0


@kwarg_bool
def run() -> Int32:
    x = takes(flag="true")
    y = takes(flag="false")
    return x + y


def main() -> None:
    print(run())


main()
