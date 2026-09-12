# Regression: a record explicitly declaring `extends Default` while having a
# required-param `__init__` (so `T{}` would fail in C++) must NOT be admitted
# as Default. Earlier shape let `_check_record_extends` accept the declaration
# after the `_is_default_constructible` predicate denied -- bypassing the
# marker and producing a cryptic C++ aggregate-init error at the first
# `make_default[Bad]()` call site. Mirrors error_copyable_extends_contradiction;
# same fix shape (predicate is authoritative for the marker arm).
from tpy import int32, Default


class Bad(Default):  # tpyc: error(/Class 'Bad' declares implementation of protocol 'Default'/)
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x
