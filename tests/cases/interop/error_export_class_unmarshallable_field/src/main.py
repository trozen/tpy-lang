# An @export class whose annotated field type doesn't cross the boundary is
# rejected: every field becomes a getset descriptor, so its type must marshal.
# tpy: ext_module
from tpy import int64
from tpy.extern import export


@export
class Bad:
    def __init__(self, items: list[int64]):
        self.items = items  # tpyc: error(/field 'items'.*cannot cross the CPython boundary/)
