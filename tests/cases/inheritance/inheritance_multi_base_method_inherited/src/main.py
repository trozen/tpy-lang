# BaseN.method(self, ...) resolves through BaseN's MRO so inherited methods
# (defined on a grandparent) are reachable even when BaseN itself doesn't
# declare them. Here 'identify' lives on Root; Middle inherits it; Leaf reaches
# Middle.identify -> Root.identify via the unbound-self form.


class Root:
    def identify(self) -> str:
        return "root"


class Middle(Root):
    pass


class Leaf(Middle):
    def delegate(self) -> str:
        # Middle does not literally define 'identify'; resolution walks
        # Middle's MRO up to Root.
        return Middle.identify(self)


def main() -> None:
    print(Leaf().delegate())


main()
