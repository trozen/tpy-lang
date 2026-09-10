# Bodied @dispatch variants coexist with @native variants in one set
from tpy import dispatch
from tpy.extern import native


@dispatch
@native("std::log")
def log(x: float) -> float: ...  # tpyc: ok

@dispatch
def log(x: float, base: float) -> float:  # tpyc: ok
    return log(x) / log(base)


def main() -> None:
    print(log(1.0))
    print(log(8.0, 2.0))


main()
