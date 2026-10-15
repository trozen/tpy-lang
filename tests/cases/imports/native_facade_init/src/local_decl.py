import sys

for i in range(1):
    # An annotation-only decl inside a module-level block declares the name
    # as `int`, so the square does not wrap.
    x: int
    x = 65536
    print("local_decl: for", x * x)

if len(sys.argv) > 0:
    # The decl in one branch types the name for both assignments.
    y: int
    y = 65536
else:
    y = 2
print("local_decl: if", y * y)

if len(sys.argv) > 0:
    # Only this branch binds the name: its decl alone must type it.
    c: int
    c = 65536
    print("local_decl: one-branch", c * c)

n = 0
while n < 1:
    # The same inside a module-level while body.
    w: int
    w = 65536
    print("local_decl: while", w * w)
    n += 1
