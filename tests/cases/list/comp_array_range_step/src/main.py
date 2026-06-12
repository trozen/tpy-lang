# 3-arg literal ranges (negative step, nontrivial start) resolve to a stack
# Array built by index arithmetic: value = start + i * step.
def main():
    down = [i for i in range(10, 1, -2)]  # tpyc: type(/Array\[Int32, 5\]/)
    print(down[0], down[4], len(down))
    up = [i * i for i in range(2, 9, 3)]  # tpyc: type(/Array\[Int32, 3\]/)
    print(up[0], up[2], len(up))
    off = [i for i in range(3, 7)]  # tpyc: type(/Array\[Int32, 4\]/)
    print(off[0], off[3])


main()
