import math
import time

from tpy import Int32

WIDTH: Int32 = 100
HEIGHT: Int32 = 50
MAX_ITER: Int32 = 1000

# Mandelbrot set bounds
X_MIN = -2.5
X_MAX = 1.0
Y_MIN = -1.25
Y_MAX = 1.25

# ASCII gradient from dark to light
CHARS = " .,:;+*?%S#@"
CHAR_SCALE: Int32 = len(CHARS) - 1
INV_LOG_MAX = 1.0 / math.log(1.0 + MAX_ITER)


def mandelbrot(cx: float, cy: float) -> Int32:
    x = 0.0
    y = 0.0
    for i in range(MAX_ITER):
        if x * x + y * y > 4.0:
            return i
        x2 = x * x - y * y + cx
        y = 2.0 * x * y + cy
        x = x2
    return MAX_ITER


def iter_to_char(iters: Int32) -> str:
    if iters >= MAX_ITER:
        return CHARS[0]
    log_val = math.log(1.0 + iters) * INV_LOG_MAX
    char_idx: Int32 = Int32(log_val * CHAR_SCALE)
    return CHARS[char_idx]


def main():
    t0 = time.time()
    dx = (X_MAX - X_MIN) / WIDTH
    dy = (Y_MAX - Y_MIN) / HEIGHT
    cy = Y_MAX
    for row in range(HEIGHT):
        cx = X_MIN
        for col in range(WIDTH):
            iters = mandelbrot(cx, cy)
            print(iter_to_char(iters), end="")
            cx = cx + dx
        print("")
        cy = cy - dy
    print("elapsed[ms]:", (time.time() - t0) * 1000)


if __name__ == "__main__":
    main()
