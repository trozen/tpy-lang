from tpy import Ptr, int32

def main() -> None:
    p: Ptr[int32] = Ptr[int32]()
    print(p.__deref__())

main()
