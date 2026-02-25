# Enum with negative values
from enum import Enum

class Signal(Enum):
    Error = -1
    Ok = 0
    Warning = 1

def main() -> None:
    s: Signal = Signal.Error
    print(s)
    print(s.value)
    s = Signal.Ok
    print(s)
    print(s.value)

main()
