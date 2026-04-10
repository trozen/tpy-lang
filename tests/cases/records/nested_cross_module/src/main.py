# Cross-module access to nested types: constructors and enum members
from shapes import Container

def main() -> None:
    c = Container(Container.Kind.A)
    print(c.kind)
    i = Container.Inner(42)
    print(i.val)

main()
