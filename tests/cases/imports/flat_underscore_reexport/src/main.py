# Explicit `from M import _x` works for underscore-prefixed names.
# `__all__` / underscore convention only filters star imports;
# explicit imports see private names.
from c import _secret_value

print(_secret_value)
