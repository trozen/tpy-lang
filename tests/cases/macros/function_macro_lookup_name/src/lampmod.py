# Imported-record source for the lookup_imported_name test.


class Lamp:
    lit: bool

    def __init__(self, lit: bool) -> None:
        self.lit = lit


# Defined here but NOT imported by main; the macro asserts it stays
# invisible to lookup_imported_name (per-module scope, not flat-global).
class Switch:
    on: bool

    def __init__(self, on: bool) -> None:
        self.on = on
