import sys
from tpy import Int32
class W:
    def __init__(self) -> None:
        pass
    def out(self) -> None:
        print("x", file=sys.stdout if True else sys.stderr)
def a1() -> None:
    with open("/dev/null", "w") as f:
        print("y", file=f)
def a2(flag: bool) -> None:
    sinks = [sys.stdout]
    print("z", file=sinks[0])
W().out(); a1(); a2(True)
