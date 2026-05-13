from tpy import Int32

# A non-Final module-level variable -- can't be used as a Final
# initializer in another module (sema rejects with a pointed
# "cross-module Final references are not yet supported" diagnostic).
COUNTER: Int32 = Int32(7)
