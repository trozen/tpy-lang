from tpy import Int32, AnyFixedInt
def conv[T: AnyFixedInt](x: T) -> Int32:
    return Int32(x)
def main() -> None:
    print(conv(Int32(4)))
main()
