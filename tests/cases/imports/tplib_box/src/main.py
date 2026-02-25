# Test importing Box[T] from tplib standard library
from tplib import Box
from tpy import Int32

def main() -> None:
    b = Box[Int32](42)
    print(b.get())
    b.set(100)
    print(b.get())

main()
