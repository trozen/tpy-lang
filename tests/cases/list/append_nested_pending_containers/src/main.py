# Appending into a list whose element is a pending DICT or SET literal exercises
# those kernel branches on the assignability path (lists cover the third).
def main() -> None:
    ds = [{1: 2}]
    ds.append({3: 4})  # tpyc: ok
    ds[0][1] = 9       # mutate a stored dict -> real container, not a copy
    print(ds)

    ss = [{1, 2}]
    ss.append({3, 4})  # tpyc: ok
    ss[0].add(9)
    print(ss)

main()
