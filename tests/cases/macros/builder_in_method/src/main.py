# Variant of cases/macros/builder_method_body that places the builder
# trace inside a user record's method. Regression guard for two things:
# (1) pass 5.5's record-method-body walk firing on a real user method
# body; (2) out-of-line method-body codegen letting the inline reference
# to a synth record (`__tpy_builder_counter_1`) compile even though the
# synth record is declared after `Holder` in the .hpp.
from tpy import Int32
from _method_builder import Counter


class Holder:
    def make(self) -> Int32:
        c = Counter()
        c.add(7)
        c.add(35)
        res = c.build()
        return res.total()


def main() -> Int32:
    h = Holder()
    print(h.make())
    return 0


main()
