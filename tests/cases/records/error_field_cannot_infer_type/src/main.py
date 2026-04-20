# Unannotated class field whose default-value expression can't be
# type-inferred. Phase F.3d.4 deferred this error from parse time to
# sema so base-resolution errors fire first. This case has a clean
# (implicit object) base, so the sema-raised "Cannot infer type for
# field 'X'" surfaces unmasked at the field line.

class Point:
    field = undefined_fn()  # tpyc: error(/Cannot infer type for field 'field'/)
