import sys
from tpy import Int32
def a1() -> None:
    print(sys.argv[0])
def a2() -> None:
    for a in sys.argv:
        print(a)
def a3() -> None:
    n = len(sys.argv)
    print(n)
def a4() -> None:
    sys.argv.append("x")
    print(len(sys.argv))
a1(); a2(); a3(); a4()
