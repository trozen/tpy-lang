from tpy import Int32, Array
def main() -> None:
    arr: Array[Int32, 3] = [1, 2, 3]
    xs = list(arr)
    print(len(xs))
main()
