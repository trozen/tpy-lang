# Regression: a @native-bound imported enum aliased to dodge a clash with a
# same-named LOCAL enum keeps its native C++ spelling (lib::Color), distinct.
from enum import IntEnum
from nativelib import Color as NativeColor


class Color(IntEnum):
    BLUE = 1
    CYAN = 2


def native_label(c: NativeColor) -> str:
    if c == NativeColor.RED:
        return "native-red"
    return "native-green"


def main() -> None:
    print(native_label(NativeColor.RED))
    print(native_label(NativeColor.GREEN))
    print(NativeColor.RED.value)
    local: Color = Color.CYAN
    print(local.name, local.value)


main()
