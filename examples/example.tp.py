from tpy import Int32, StaticList, Ptr

class Data:
    value: Int32 = 0

    def __init__(self, v: Int32 = 0):
        self.value = v


@noalloc
def algo_function(l: StaticList[Data, 1024]) -> None:
    l.append(Data(123))
    x: Ptr[Data] = l.push_empty()
    x.value = 666
    z = Data()
    z.value = Int32(123) + Int32(321)
    l.set(0, z)
