# Test that mutable Span[T] supports element assignment via __setitem__.
from tpy import Int32, Span, Array

def set_first(s: Span[Int32], val: Int32) -> None:
    s[0] = val

def main() -> None:
    arr = Array[Int32, 3]([10, 20, 30])
    set_first(arr, 42)
    print(arr[0])
    print(arr[1])
    print(arr[2])

main()
