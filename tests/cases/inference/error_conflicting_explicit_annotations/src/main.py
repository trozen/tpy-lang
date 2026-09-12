from tpy import int32

x: int32 = int32(1)
x: int = 2  # tpyc: error(/Conflicting explicit annotations for 'x'/)
