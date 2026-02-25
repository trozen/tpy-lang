# All enum values are truthy (even value 0)
from enum import Enum

class Signal(Enum):
    Off = 0
    On = 1

def check(s: Signal) -> None:
    if s:
        print("truthy")
    else:
        print("falsy")

def main() -> None:
    check(Signal.Off)
    check(Signal.On)

main()
