# Tests explicit Span/Span[readonly[T]] construction from containers
from tpy import Int32, Array, Span, readonly

def main() -> None:
    lst: list[Int32] = [10, 20, 30]
    ros: Span[readonly[Int32]] = Span[readonly[Int32]](lst)
    print(len(ros))
    print(ros[0])

    arr: Array[Int32, 3] = [1, 2, 3]
    s: Span[Int32] = Span[Int32](arr)
    print(len(s))
    s[0] = 99
    print(arr[0])

    lst2: list[Int32] = [5, 6]
    ros2: Span[readonly[Int32]] = Span[readonly[Int32]](lst2)
    print(len(ros2))
    print(ros2[0])

main()
