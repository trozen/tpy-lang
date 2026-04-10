# Two-level nested class definitions
from tpy import Int32

class Outer:
    class Mid:
        class Deep:
            val: Int32

            def __init__(self, val: Int32) -> None:
                self.val = val

        name: str

        def __init__(self, name: str) -> None:
            self.name = name

    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

def main() -> None:
    o = Outer(1)
    print(o.x)

    m = Outer.Mid("hello")
    print(m.name)

    d = Outer.Mid.Deep(99)
    print(d.val)

main()
