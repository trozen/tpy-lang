from tpy import int32

# A non-Final module-level variable -- can't be used as a Final
# initializer in another module (sema rejects with a pointed
# "cross-module Final references are not yet supported" diagnostic).
COUNTER: int32 = int32(7)
