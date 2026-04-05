# keyword-only parameters, some required (no default)
from tpy import Int32

def format_value(value: Int32, *, width: Int32, fill: str = " ") -> str:
    s = str(value)
    while len(s) < width:
        s = fill + s
    return s

def main() -> None:
    print(format_value(42, width=5))
    print(format_value(7, width=3, fill="0"))

main()
