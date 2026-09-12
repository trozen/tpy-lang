# UninitArrayStorage used without importing from tpy.mem should error.
from tpy import int32, uint32

storage = UninitArrayStorage[int32, 4]()  # tpyc: error(/Undefined variable/)
storage.init(uint32(0), int32(1))
