from tpy import int32, Array
def main() -> None:
    arr: Array[int32, 3] = [1, 2, 3]
    xs = list(arr)
    print(len(xs))
main()
