# Unannotated literals default to int32 -- adding two values that
# individually fit but overflow together should panic at runtime.
x = 2000000000
y = 2000000000
z = x + y
print(z)
