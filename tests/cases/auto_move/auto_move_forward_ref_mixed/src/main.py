# Only Own[T] params get forwarding refs (T&&), not regular generic params.
from tpy import Int32, Own


class Box:
    value: Int32


def mixed[T](x: Own[T], y: T) -> None:
    pass


def main():
    b1 = Box()
    b1.value = 10
    b2 = Box()
    b2.value = 20
    mixed[Box](b1, b2)
    print("done")
