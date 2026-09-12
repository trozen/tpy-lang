# Records without __init__ reject positional constructor arguments.
from tpy import int32

class Foo:
    x: int32
    y: int32

def main() -> None:
    f = Foo(1, 2)  # tpyc: error(/has no __init__/)

main()
