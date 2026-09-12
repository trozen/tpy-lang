# Test importing Box[T] from tplib standard library
from tplib import Box
from tpy import int32

def main() -> None:
    b = Box[int32](42)
    print(b.get())
    b.set(100)
    print(b.get())

main()
