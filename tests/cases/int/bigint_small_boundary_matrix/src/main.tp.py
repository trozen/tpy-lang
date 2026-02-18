# Verifies arithmetic behavior when crossing the int63 small-int/big-int boundary.
# Focuses on small/large operand combinations and boundary canonicalization.

from tpy import Int64


def show_mul(label: str, a: int, b: int) -> None:
    print(label)
    print(a)
    print(b)
    print(a * b)


B: int = 1 << 62
SMALL_MAX: int = B - 1
SMALL_MIN: int = -B
BIG_POS: int = B
BIG_NEG: int = -B - 1

# small * small -> small
show_mul("mul_small_small_stays_small", SMALL_MAX, 1)
show_mul("mul_small_small_zero", SMALL_MIN, 0)

# small * small -> large (cross boundary)
show_mul("mul_small_small_to_large_pos", SMALL_MAX, 2)
show_mul("mul_small_small_to_large_neg", SMALL_MIN, -1)

# small * large -> large
show_mul("mul_small_large_to_large_1", SMALL_MAX, BIG_POS)
show_mul("mul_small_large_to_large_2", -1, BIG_POS)

# large * small -> large (or exact-boundary canonicalization to small)
show_mul("mul_large_small_large", BIG_POS, 2)
show_mul("mul_large_small_back_to_small", BIG_POS, -1)

# large * large -> large
show_mul("mul_large_large", BIG_POS, BIG_NEG)

# Add/sub around boundary.
print("add_sub_boundary")
print(SMALL_MAX + 1)      # small -> large
print(BIG_POS - 1)        # large -> small
print(BIG_NEG + 1)        # large -> small (SMALL_MIN)
print(SMALL_MIN - 1)      # small -> large
print(BIG_POS + BIG_NEG)  # exact small result (-1)
print(BIG_POS - BIG_POS)  # exact small result (0)

# Division/modulo around boundary.
print("div_mod_boundary")
print(BIG_POS // 2)         # small
print(BIG_POS % 2)          # small
print((BIG_NEG + 1) // 2)   # small
print((BIG_NEG + 1) % 2)    # small

# Fixed-width conversion sanity around boundary values.
print("fixed_width_conversions")
print(Int64(SMALL_MAX))
print(Int64(SMALL_MIN))
print(Int64(BIG_POS - 1))
print(Int64(BIG_NEG + 1))
