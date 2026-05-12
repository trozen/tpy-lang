# Helper module: a user-defined generic record providing pointer indirection
# via a `_ptr: Ptr[T]` field. Used by main.py's recursive union to exercise
# the structural field-walk path in cycle detection (no hard-coded name).
# Kept minimal -- no __del__ / clone -- because this test only exercises
# cycle detection, not heap ownership.
from __future__ import annotations
from tpy import Ptr


class MyWrap[T]:
    _ptr: Ptr[T]
