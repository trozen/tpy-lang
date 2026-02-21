# Too many explicit type args for a generic function.
def identity[T](x: T) -> T:
    return x

def main() -> None:
    y = identity[int, str](42)  # tpyc: error(/expects 1 type argument/)

main()
