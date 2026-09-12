# Two-level nested class definitions
from tpy import int32

class Outer:
    class Mid:
        class Deep:
            val: int32

            def __init__(self, val: int32) -> None:
                self.val = val

        name: str

        def __init__(self, name: str) -> None:
            self.name = name

    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

def main() -> None:
    o = Outer(1)
    print(o.x)

    m = Outer.Mid("hello")
    print(m.name)

    d = Outer.Mid.Deep(99)
    print(d.val)

main()
