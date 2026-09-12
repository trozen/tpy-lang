# Tests explicit Span/Span[readonly[T]] construction from containers
from tpy import int32, Array, Span, readonly

def main() -> None:
    lst: list[int32] = [10, 20, 30]
    ros: Span[readonly[int32]] = Span[readonly[int32]](lst)
    print(len(ros))
    print(ros[0])

    arr: Array[int32, 3] = [1, 2, 3]
    s: Span[int32] = Span[int32](arr)
    print(len(s))
    s[0] = 99
    print(arr[0])

    lst2: list[int32] = [5, 6]
    ros2: Span[readonly[int32]] = Span[readonly[int32]](lst2)
    print(len(ros2))
    print(ros2[0])

main()
