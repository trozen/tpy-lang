# Zero-arg construction of a record without __init__ still works
# (C++ default construction).
from tpy import int32

class Foo:
    x: int32
    y: int32

def main() -> None:
    f = Foo()
    print(f.x)
    print(f.y)

main()
