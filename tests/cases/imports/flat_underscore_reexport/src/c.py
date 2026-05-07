from tpy import Int32

# Underscore-prefixed name is private by convention but explicit
# imports still see it.
_secret_value: Int32 = Int32(99)
