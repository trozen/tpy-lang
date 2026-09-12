# tpy: ext_module
# Exposed-class dunders: container protocol. __len__ ->
# Py_mp_length/Py_sq_length; __getitem__ -> Py_mp_subscript; __setitem__ ->
# Py_mp_ass_subscript (Box has no __delitem__, so `del box[i]` is rejected --
# ext-only, see ext_checks.py; Slot below defines BOTH, exercising the
# actual deletion dispatch); __contains__ -> Py_sq_contains; __iter__ +
# __next__ -> Py_tp_iter/Py_tp_iternext (__next__ is implicitly
# @error_return(StopIteration), unwrapped by the glue instead of thrown).
from __future__ import annotations
from tpy import int32, int64, Own
from tpy.extern import export


@export
class Box:
    a: int64
    b: int64
    c: int64
    _i: int32

    def __init__(self, a: int64, b: int64, c: int64):
        self.a = a
        self.b = b
        self.c = c
        self._i = 0

    def __len__(self) -> int32:
        return 3

    def __getitem__(self, i: int32) -> int64:
        if i == 0:
            return self.a
        if i == 1:
            return self.b
        return self.c

    def __setitem__(self, i: int32, value: int64) -> None:
        if i == 0:
            self.a = value
        elif i == 1:
            self.b = value
        else:
            self.c = value

    def __contains__(self, value: int64) -> bool:
        return value == self.a or value == self.b or value == self.c

    def __iter__(self) -> Own[Box]:
        return Box(self.a, self.b, self.c)

    def __next__(self) -> int32:
        if self._i < 3:
            result = self._i
            self._i += 1
            return result
        raise StopIteration


@export
class Slot:
    value: int64
    filled: bool

    def __init__(self, value: int64):
        self.value = value
        self.filled = True

    def __len__(self) -> int32:
        return 1 if self.filled else 0

    def __getitem__(self, i: int32) -> int64:
        if not self.filled:
            raise KeyError(str(i))
        return self.value

    def __setitem__(self, i: int32, value: int64) -> None:
        self.value = value
        self.filled = True

    def __delitem__(self, i: int32) -> None:
        self.filled = False
