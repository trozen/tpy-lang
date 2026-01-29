from tpy import Int32, StaticList, Ptr, noalloc

class Data:
    value: Int32
    other_value = Int32(0)
    
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

lst = StaticList[Data, 1024]()  # works (Data is defined)
algo_function(lst)
print(lst[0].value, len(lst))  # -> Int32(444) 2
