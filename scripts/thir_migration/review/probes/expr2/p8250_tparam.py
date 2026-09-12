from tpy import int32, AnyFixedInt
def conv[T: AnyFixedInt](x: T) -> int32:
    return int32(x)
def main() -> None:
    print(conv(int32(4)))
main()
