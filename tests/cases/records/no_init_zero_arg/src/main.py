# Zero-arg construction of a record without __init__ still works
# (C++ default construction).
from tpy import Int32

class Foo:
    x: Int32
    y: Int32

def main() -> None:
    f = Foo()
    print(f.x)
    print(f.y)

main()
