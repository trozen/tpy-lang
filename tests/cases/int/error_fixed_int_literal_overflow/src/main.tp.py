# Out-of-range literal in fixed-int constructor should be a compile error
from tpy import Int8, UInt8

ok: Int8 = Int8(127)           # tpyc: ok
ok2: Int8 = Int8(-128)         # tpyc: ok
ok3: UInt8 = UInt8(0)          # tpyc: ok
bad: UInt8 = UInt8(-3)         # tpyc: error(/UInt8 overflow.*outside range/)
