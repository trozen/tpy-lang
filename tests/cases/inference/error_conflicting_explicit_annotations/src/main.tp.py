from tpy import Int32

x: Int32 = Int32(1)
x: int = 2  # tpyc: error(/Conflicting explicit annotations for 'x'/)
