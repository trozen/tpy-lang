# @overload + @native: stub-only overload groups with different C++ targets
from typing import overload
from tpy.extern import native

@overload
@native("std::log")
def log(x: float) -> float: ...

@overload
@native("tpy::math::log_base")
def log(x: float, base: float) -> float: ...

def main() -> None:
    print(log(1.0))
    print(log(8.0, 2.0))

main()
