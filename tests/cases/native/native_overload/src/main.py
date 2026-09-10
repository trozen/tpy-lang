# @dispatch + @native: native-only variant sets with different C++ targets
from tpy import dispatch
from tpy.extern import native, cpp_template

@native("std::log")
def _log(x: float) -> float: ...

@dispatch
@native("std::log")
def log(x: float) -> float: ...

@dispatch
@cpp_template("std::log({0}) / std::log({1})")
def log(x: float, base: float) -> float: ...

def main() -> None:
    print(log(1.0))
    print(log(8.0, 2.0))

main()
