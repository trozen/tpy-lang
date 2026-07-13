# An @export class inheriting an exposed class from another module is
# rejected: the base's C++ name and CPython type handle are keyed to the
# defining module's glue TU (cross-module exposed types are deferred).
# tpy: ext_module
from tpy import Int64
from tpy.extern import export
from base_mod import Imported


@export
class Derived(Imported):  # tpyc: error(/base class 'Imported' is defined in another module/)
    def get(self) -> Int64:
        return self.x
