import math
import time

WIDTH = 100
HEIGHT = 50
MAX_ITER = 1000

# Mandelbrot set bounds
X_MIN, X_MAX = -2.5, 1.0
Y_MIN, Y_MAX = -1.25, 1.25

# ASCII gradient from dark to light
CHARS = " .,:;+*?%S#@"


def mandelbrot(cx: float, cy: float) -> int:
    x, y = 0.0, 0.0
    for i in range(MAX_ITER):
        if x * x + y * y > 4.0:
            return i
        x, y = x * x - y * y + cx, 2.0 * x * y + cy
    return MAX_ITER


def iter_to_char(iters: int) -> str:
    if iters >= MAX_ITER:
        return CHARS[0]
    # Logarithmic scaling spreads low iteration counts across more characters
    log_val = math.log(1.0 + iters) / math.log(1.0 + MAX_ITER)
    char_idx = int(log_val * (len(CHARS) - 1))
    return CHARS[char_idx]


def main():
    t0 = time.time()
    for row in range(HEIGHT):
        cy = Y_MAX - (Y_MAX - Y_MIN) * row / HEIGHT
        for col in range(WIDTH):
            cx = X_MIN + (X_MAX - X_MIN) * col / WIDTH
            iters = mandelbrot(cx, cy)
            print(iter_to_char(iters), end="")
        print("")
    print("elapsed[ms]:", (time.time() - t0) * 1000)


if __name__ == "__main__":
    main()
