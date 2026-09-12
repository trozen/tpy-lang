from tpy import int32

# Underscore-prefixed name is private by convention but explicit
# imports still see it.
_secret_value: int32 = int32(99)
