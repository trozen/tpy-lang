from tpy import Ptr, Int32

def main() -> None:
    p: Ptr[Int32] = Ptr[Int32]()
    print(p.__deref__())

main()
