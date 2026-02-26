# IntEnum truthiness: value 0 is falsy, non-zero is truthy
from enum import IntEnum

class Status(IntEnum):
    Off = 0
    On = 1
    Standby = 2

def main() -> None:
    # Value 0 is falsy
    if Status.Off:
        print("off is truthy")
    else:
        print("off is falsy")

    # Non-zero is truthy
    if Status.On:
        print("on is truthy")
    else:
        print("on is falsy")

    # not operator
    print(not Status.Off)
    print(not Status.On)

main()
