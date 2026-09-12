# Test Box[T].take() -- consuming method that moves out the value and destroys the box.
from tplib.box import Box
from tpy import int32

def main() -> None:
    b: Box[int32] = Box(int32(42))
    val: int32 = b.take()
    print(val)

    # Temporary receiver
    print(Box(int32(99)).take())

main()
