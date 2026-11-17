# Outside the cycle: binds value in a compound statement.
value: int
try:
    value = 7
except ValueError:
    value = 8
