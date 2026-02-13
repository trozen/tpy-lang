from tpy import StaticList, Int32
x: StaticList[Int32, 4] = StaticList[Int32, 4](["a"])  # tpyc: error(/cannot be constructed from/)
