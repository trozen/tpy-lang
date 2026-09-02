from tpy import Int32, Array
def a(arr: Array[Int32, 3], f: bool) -> Array[Int32, 3] | None:
    if f:
        return arr
    return None
def main() -> None:
    arr: Array[Int32, 3] = [1, 2, 3]
    print(1 if a(arr, True) is None else 0)
main()
