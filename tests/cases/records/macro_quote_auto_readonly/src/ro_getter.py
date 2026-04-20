# tpy: macro_module
"""Regression: macros can emit @auto_readonly methods.

Pins Phase F.3b.6.6 (FragmentParser method mode): `add_method_from_source`
must route through `_parse_method` so `has_auto_readonly_decorator` is
set, and `expand_methods_for_record` then clones the method into
mutable + const halves via sema.method_expansion.
"""
from tpyc.macro_api import ClassInfo, class_macro


@class_macro
def ro_getter(cls: ClassInfo) -> None:
    """Emit an __init__ and a readonly-adapting getter for the first field."""
    assert cls.fields, "ro_getter requires at least one field"
    f0 = cls.fields[0]
    cls.add_method_from_source(f"""
        def __init__(self, value: {f0.type.name}) -> None:
            self.{f0.name} = value
    """)
    cls.add_method_from_source(f"""
        @auto_readonly
        def first(self) -> {f0.type.name}:
            return self.{f0.name}
    """)
