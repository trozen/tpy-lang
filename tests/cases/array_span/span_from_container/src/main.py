# Tests explicit Span/ReadOnlySpan construction from containers
from tpy import Int32, Array, StaticList, ReadOnlySpan, Span

def main() -> None:
    lst: list[Int32] = [10, 20, 30]
    ros: ReadOnlySpan[Int32] = ReadOnlySpan[Int32](lst)
    print(len(ros))
    print(ros[0])

    arr: Array[Int32, 3] = [1, 2, 3]
    s: Span[Int32] = Span[Int32](arr)
    print(len(s))
    s[0] = 99
    print(arr[0])

    sl: StaticList[Int32, 4] = StaticList[Int32, 4]([5, 6])
    ros2: ReadOnlySpan[Int32] = ReadOnlySpan[Int32](sl)
    print(len(ros2))
    print(ros2[0])

main()
