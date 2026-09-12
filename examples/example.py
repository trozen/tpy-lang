from tpy import int32, noalloc
from tplib import ArrayList

class Data:
    value: int32
    other_value = int32(0)

    def __init__(self, v: int32 = 0):
        self.value = v

@noalloc
def algo_function(l: ArrayList[Data, 1024]) -> None:
    l.append(Data(123))
    l.append(Data())
    l[1].value = 666
    z = Data()
    z.value = int32(123) + int32(321)
    l[0] = z

lst = ArrayList[Data, 1024]()
algo_function(lst)
print(lst[0].value, len(lst))  # -> int32(444) 2
