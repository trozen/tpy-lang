# Variant of cases/macros/builder_method_body that adds a user record
# (Holder) so pass 5.5's record-method-body walk fires on a real user
# method body. The builder trace itself stays in a free function
# (`compute`) because emitting one inside an inline user method body
# trips a C++ codegen ordering issue (see BUGS.md "synth record after
# user record breaks inline method use").
from tpy import Int32
from _method_builder import Counter


def compute() -> Int32:
    c = Counter()
    c.add(7)
    c.add(35)
    res = c.build()
    return res.total()


class Holder:
    def make(self) -> Int32:
        return compute()


def main() -> Int32:
    h = Holder()
    print(h.make())
    return 0


main()
