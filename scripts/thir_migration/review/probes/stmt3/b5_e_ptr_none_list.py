from tpy import int32, Ptr
def main() -> None:
    p: Ptr[list[int32]] = None
    print(1 if p is None else 0)
main()
