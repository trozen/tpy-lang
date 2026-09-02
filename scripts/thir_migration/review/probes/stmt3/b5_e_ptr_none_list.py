from tpy import Int32, Ptr
def main() -> None:
    p: Ptr[list[Int32]] = None
    print(1 if p is None else 0)
main()
