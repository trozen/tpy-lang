# Nested def whose param is an imported user record. Pins Phase F.5
# behaviour for parse-time resolver calls (nested-def refs are resolved
# at parse time, before F.5.1 canonicalizes the import table), so the
# codegen path that reads _module_qname for C++ namespace qualification
# still sees the defining module.
from tpy import int32
from widgets import Widget


def make_value() -> int32:
    def inner(w: Widget) -> int32:
        return w.get()
    obj = Widget(int32(42))
    return inner(obj)


def main() -> None:
    print(make_value())


main()
