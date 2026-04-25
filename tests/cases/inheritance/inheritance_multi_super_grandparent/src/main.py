# Only the grandparent (GA) defines tag(); A inherits without overriding.
# super().tag() skips past A and codegen emits this->GA::tag() directly.


class GA:
    def tag(self) -> str:
        return "GA.tag"


class A(GA):
    pass


class B:
    pass


class C(A, B):
    def tag(self) -> str:
        return super().tag() + " + child"


def main() -> None:
    c = C()
    print(c.tag())


main()
