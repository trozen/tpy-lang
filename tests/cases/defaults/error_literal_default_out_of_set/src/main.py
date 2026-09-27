# A `Literal[...]` parameter's default must be one of the declared values; the
# error names the offending value, not its widened `str` type.
from typing import Literal


def open_mode(mode: Literal["r", "rb", "r+b", "rb+"] = "wb") -> str:  # tpyc: error(/expected Literal\["r", "rb", "r\+b", "rb\+"\], got "wb"/)
    return mode


def main() -> None:
    print(open_mode())


main()
