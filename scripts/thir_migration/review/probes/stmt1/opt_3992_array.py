from tpy import int32, Array
def main() -> None:
    a: Array[int32, 2] | None = None
    if a is None:
        print("none")
main()
