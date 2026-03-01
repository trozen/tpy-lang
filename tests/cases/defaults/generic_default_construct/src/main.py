# T() default-construction syntax for generic type parameters
from tpy import Int32

def make_default[T](x: T = T()) -> T:
    return x

def main() -> None:
    a: Int32 = make_default[Int32]()
    print(a)

    b: str = make_default[str]()
    print(b)
    print(len(b))

    c: bool = make_default[bool]()
    print(c)

    d: Int32 = make_default[Int32](42)
    print(d)

    # T inferred from arg
    e: Int32 = make_default(42)
    print(e)

main()
