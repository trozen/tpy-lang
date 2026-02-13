from tpy import StaticList, Int32
x: StaticList[Int32, 4] = StaticList[Int32, 4](Int32(1))  # tpyc: error(/cannot be constructed from/)
