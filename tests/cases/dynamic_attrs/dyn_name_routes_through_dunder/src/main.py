# D16 phase 9 (Option A): when getattr(obj, name_var) is called with a
# runtime name that happens to match a declared field name, the builtin
# still routes through __getattr__ rather than reading the field.
# CPython divergence -- documented in DYNAMIC_ATTRS_DESIGN.md divergence #8.


class Hybrid:
    declared: str

    def __init__(self, declared: str) -> None:
        self.declared = declared

    def __getattr__(self, name: str) -> str:
        # Reports who got asked, distinct from the field's value.
        return "dunder:" + name


def main() -> None:
    h = Hybrid("field-value")
    # Direct access reads the declared field.
    print(h.declared)
    # Runtime-name access goes through __getattr__ even though "declared"
    # is a real field. CPython would print "field-value" here; TPy prints
    # "dunder:declared".
    name = "declared"
    print(getattr(h, name, "fallback"))


main()
