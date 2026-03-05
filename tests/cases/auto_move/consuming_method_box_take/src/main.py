# Test Box[T].take() -- consuming method that moves out the value and destroys the box.
from tplib.box import Box
from tpy import Int32

def main() -> None:
    b: Box[Int32] = Box(Int32(42))
    val: Int32 = b.take()
    print(val)

    # Temporary receiver
    print(Box(Int32(99)).take())

main()
