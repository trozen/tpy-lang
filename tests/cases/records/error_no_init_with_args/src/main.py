# Records without __init__ reject positional constructor arguments.
from tpy import Int32

class Foo:
    x: Int32
    y: Int32

def main() -> None:
    f = Foo(1, 2)  # tpyc: error(/has no __init__/)

main()
