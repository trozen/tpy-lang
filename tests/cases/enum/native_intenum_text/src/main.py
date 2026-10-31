# A @native IntEnum's text follows CPython's IntEnum model with the C++
# values: str() text is the value, repr text is `<Level.Low: 10>`.
__tpy__ = {"includes": ["native_types.hpp"]}
from enum import IntEnum, auto
from tpy.extern import native


@native("ns::Level")
class Level(IntEnum):
    Low = auto()
    High = auto()


def main() -> None:
    lv = Level.High
    print("print", lv)  # tpyc: ok
    print("fstring", f"{lv}", f"[{lv:>4}]", f"{lv!r}")  # tpyc: ok
    print("repr", repr(lv))  # tpyc: ok
    print("list", [Level.Low, lv])  # tpyc: ok


main()
