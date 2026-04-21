# When `from pkg import Foo` resolves, it should pick up pkg.__init__.py's
# local `class Foo` (tag="from_init"), not pkg.sub.Foo (tag="from_sub").
from pkg import Foo

def main() -> None:
    f = Foo()
    print(f.tag)

main()
