# T() default-construction in methods with class-level type params
from tpy import Int32, copy

class Container[T]:
    val: T

    def __init__(self, val: T) -> None:
        self.val = copy(val)

    def get_or_default(self, fallback: T = T()) -> T:
        return fallback

def main() -> None:
    c = Container[Int32](10)
    print(c.get_or_default())
    print(c.get_or_default(99))

main()
