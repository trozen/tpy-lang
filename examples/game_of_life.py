from tpy import Int32, StaticList

# Compile-time constants
WIDTH: Int32 = 10
HEIGHT: Int32 = 8

class Grid:
    cells: StaticList[Int32, 80]

    def __init__(self):
        self.cells = StaticList[Int32, 80]()
        for i in range(80):
            self.cells.append(0)

    def idx(self, x: Int32, y: Int32) -> Int32:
        return y * WIDTH + x

    def get(self, x: Int32, y: Int32) -> Int32:
        if x < 0:
            return 0
        if x >= WIDTH:
            return 0
        if y < 0:
            return 0
        if y >= HEIGHT:
            return 0
        return self.cells[self.idx(x, y)]

    def set(self, x: Int32, y: Int32, val: Int32) -> None:
        self.cells[self.idx(x, y)] = val

def count_neighbors(g: Grid, x: Int32, y: Int32) -> Int32:
    n = 0
    n = n + g.get(x - 1, y - 1)
    n = n + g.get(x,     y - 1)
    n = n + g.get(x + 1, y - 1)
    n = n + g.get(x - 1, y)
    n = n + g.get(x + 1, y)
    n = n + g.get(x - 1, y + 1)
    n = n + g.get(x,     y + 1)
    n = n + g.get(x + 1, y + 1)
    return n

def step(src: Grid, dst: Grid) -> None:
    for y in range(HEIGHT):
        for x in range(WIDTH):
            n = count_neighbors(src, x, y)
            alive = src.get(x, y)

            if alive == 1 and (n == 2 or n == 3):
                dst.set(x, y, 1)
            elif alive == 0 and n == 3:
                dst.set(x, y, 1)
            else:
                dst.set(x, y, 0)

def print_grid(g: Grid) -> None:
    for y in range(HEIGHT):
        for x in range(WIDTH):
            if g.get(x, y) == 1:
                print("#", end="")
            else:
                print(".", end="")
        print("")
    print("")

# Glider
a = Grid()
b = Grid()

a.set(1, 0, 1)
a.set(2, 1, 1)
a.set(0, 2, 1)
a.set(1, 2, 1)
a.set(2, 2, 1)

for i in range(5):
    print_grid(a)
    step(a, b)
    print_grid(b)
    step(b, a)
