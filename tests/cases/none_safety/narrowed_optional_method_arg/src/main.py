# Narrowed Optional passed as argument to method on another object.
# Verifies that value-optional unwrap (*x) is emitted in method call codegen.

class Printer:
    def show(self, s: str) -> None:
        print(s)

    def show_int(self, n: int) -> None:
        print(n)

    def process(self, x: str | None) -> None:
        if x is not None:
            self.show(x)

class Node:
    label: str | None
    def __init__(self, label: str | None = None) -> None:
        self.label = label

def test_method_arg(x: str | None, p: Printer) -> None:
    if x is not None:
        p.show(x)

def test_method_arg_int(x: int | None, p: Printer) -> None:
    if x is not None:
        p.show_int(x)

def test_self_call(p: Printer) -> None:
    p.process("self-call")
    p.process(None)

def main() -> None:
    p = Printer()
    test_method_arg("hello", p)
    test_method_arg(None, p)
    test_method_arg_int(42, p)
    test_method_arg_int(None, p)
    test_self_call(p)

main()
