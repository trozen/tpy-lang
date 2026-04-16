# Mode (b) can coexist with @native stubs in the same overload group
from typing import overload
from tpy.extern import native


@overload
@native("std::log")
def log(x: float) -> float: ...  # tpyc: ok

@overload
def log(x: float, base: float) -> float:  # tpyc: ok
    return log(x) / log(base)


def main() -> None:
    print(log(1.0))
    print(log(8.0, 2.0))


main()
