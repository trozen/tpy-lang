from tpy import Int32, Array
def main() -> None:
    a: Array[Int32, 2] | None = None
    if a is None:
        print("none")
main()
