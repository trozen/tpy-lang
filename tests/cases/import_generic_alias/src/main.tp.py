from tpy import StaticList as SL

x: SL[int, 3] = SL[int, 3]()
x.append(1)
x.append(2)
print(len(x))
print(x[0])
print(x[1])
