# T() default with kwargs gap-filling: f(1, c=3) skips b: T = T()
from tpy import int32

def three_params[T](a: T, b: T = T(), c: T = T()) -> None:
    print(a)
    print(b)
    print(c)

def main() -> None:
    three_params[int32](10, c=5)
    three_params[int32](1, 2, 3)
    # T inferred from first arg
    three_params(42)

main()
