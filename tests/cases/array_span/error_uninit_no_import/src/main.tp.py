# UninitArrayStorage used without importing from tpy.mem should error.
from tpy import Int32, UInt32

storage = UninitArrayStorage[Int32, 4]()
storage.init(UInt32(0), Int32(1))  # tpyc: error(/Cannot call method/)
