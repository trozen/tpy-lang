/**
 * Fixed-width integer check self-check.
 *
 * Pins the runtime rules generated code relies on:
 * - every `*_check<T>` helper is instantiated at each fixed width AND at
 *   BigInt (an 8/16-bit operator computes in the default int, which may be
 *   BigInt), and the BigInt instantiation is exact;
 * - a fixed-width overflow raises a catchable OverflowError, and a division
 *   by zero a ZeroDivisionError, at each width;
 * - a byte from any integer raises ValueError outside range(0, 256);
 * - a lookup compares integers of different types by value (`key_eq`,
 *   `seq_contains`, ordered_map / ordered_set), never by a wrapping
 *   conversion.
 *
 * No snapshot case reaches the BigInt instantiations at every width, nor the
 * mixed-type lookup keys sema normally converts first.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <cstdint>
#include <cstdio>
#include <vector>

#include "tpy/tpy.hpp"

namespace {

int failures = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

template<typename F>
bool raises_overflow(F&& f) {
    try {
        f();
    } catch (const tpy::OverflowError&) {
        return true;
    }
    return false;
}

template<typename F>
bool raises_zero_division(F&& f) {
    try {
        f();
    } catch (const tpy::ZeroDivisionError&) {
        return true;
    }
    return false;
}

template<typename F>
bool raises_value_error(F&& f) {
    try {
        f();
    } catch (const tpy::ValueError&) {
        return true;
    }
    return false;
}

// Each width: the promoted call shape `op_check<T>(narrow, narrow)` with T a
// fixed width, and the overflow it reports.
template<typename T>
void fixed_width_overflows(const char* what) {
    constexpr T hi = std::numeric_limits<T>::max();
    constexpr T lo = std::numeric_limits<T>::min();
    if (!raises_overflow([&] { (void)tpy::add_check<T>(hi, T(1)); })) {
        std::printf("FAIL: %s add overflow raises OverflowError\n", what);
        ++failures;
    }
    if (!raises_overflow([&] { (void)tpy::mul_check<T>(hi, T(2)); })) {
        std::printf("FAIL: %s mul overflow raises OverflowError\n", what);
        ++failures;
    }
    if (!raises_overflow([&] { (void)tpy::sub_check<T>(lo, T(1)); })) {
        std::printf("FAIL: %s sub overflow raises OverflowError\n", what);
        ++failures;
    }
    if (!raises_overflow([&] { (void)tpy::lshift_check<T>(hi, 1); })) {
        std::printf("FAIL: %s lshift overflow raises OverflowError\n", what);
        ++failures;
    }
    if (!raises_overflow([&] { (void)tpy::pow_check<T>(hi, T(2)); })) {
        std::printf("FAIL: %s pow overflow raises OverflowError\n", what);
        ++failures;
    }
    if (!raises_zero_division([&] { (void)tpy::div_check<T>(hi, T(0)); })) {
        std::printf("FAIL: %s div by zero raises ZeroDivisionError\n", what);
        ++failures;
    }
    if (!raises_zero_division([&] { (void)tpy::mod_check<T>(hi, T(0)); })) {
        std::printf("FAIL: %s mod by zero raises ZeroDivisionError\n", what);
        ++failures;
    }
    if (tpy::div_check<T>(T(7), T(2)) != T(3) || tpy::mod_check<T>(T(7), T(2)) != T(1)) {
        std::printf("FAIL: %s div / mod in range\n", what);
        ++failures;
    }
    if constexpr (std::is_signed_v<T>) {
        if (!raises_overflow([&] { (void)tpy::neg_check<T>(lo); })) {
            std::printf("FAIL: %s negation of the minimum raises OverflowError\n", what);
            ++failures;
        }
        if (!raises_overflow([&] { (void)tpy::div_check<T>(lo, T(-1)); })) {
            std::printf("FAIL: %s minimum // -1 raises OverflowError\n", what);
            ++failures;
        }
        if (tpy::div_check<T>(T(-7), T(2)) != T(-4) || tpy::mod_check<T>(T(-7), T(2)) != T(1)) {
            std::printf("FAIL: %s floor div / mod of a negative\n", what);
            ++failures;
        }
        if (tpy::neg_check<T>(T(5)) != T(-5)) {
            std::printf("FAIL: %s negation in range\n", what);
            ++failures;
        }
    }
    // A byte from this width: CPython's ValueError outside range(0, 256).
    if constexpr (sizeof(T) > 1 || std::is_signed_v<T>) {
        if (!raises_value_error([&] { (void)tpy::int_to_byte(lo < 0 ? lo : hi); })) {
            std::printf("FAIL: %s int_to_byte out of range raises ValueError\n", what);
            ++failures;
        }
    }
    check(tpy::int_to_byte(T(65)) == 65, "int_to_byte in range");
    check(tpy::add_check<T>(T(1), T(2)) == T(3), "fixed add in range");
}

// bool and the character types are not integers a byte is made from.
template<typename T>
concept makes_byte = requires(T v) { tpy::int_to_byte(v); };
static_assert(makes_byte<int32_t> && makes_byte<uint64_t>);
static_assert(!makes_byte<bool> && !makes_byte<char> && !makes_byte<char8_t>
              && !makes_byte<char32_t> && !makes_byte<wchar_t>);

}  // namespace

int main() {
    using tpy::BigInt;

    fixed_width_overflows<int8_t>("int8");
    fixed_width_overflows<int16_t>("int16");
    fixed_width_overflows<int32_t>("int32");
    fixed_width_overflows<int64_t>("int64");
    fixed_width_overflows<uint8_t>("uint8");
    fixed_width_overflows<uint16_t>("uint16");
    fixed_width_overflows<uint32_t>("uint32");
    fixed_width_overflows<uint64_t>("uint64");

    check(raises_overflow([] { (void)tpy::neg_check<int32_t>(INT32_MIN); }),
          "int32 negation overflow raises OverflowError");
    check(raises_overflow([] { (void)tpy::int_cast_check<int8_t>(int32_t{300}); }),
          "int_cast_check out of range raises OverflowError");
    check(raises_overflow([] { (void)BigInt(300).to_fixed_check<int8_t>(); }),
          "BigInt to_fixed_check out of range raises OverflowError");
    check(raises_overflow([] { (void)tpy::int_cast_check<uint32_t>(int32_t{-1}); }),
          "a negative value into an unsigned slot raises OverflowError");

    check(raises_value_error([] { (void)tpy::int_to_byte(BigInt(256)); }),
          "int_to_byte of a BigInt out of range raises ValueError");
    check(tpy::int_to_byte(BigInt(255)) == 255, "int_to_byte of a BigInt in range");

    // Promoted 8/16-bit operands at T = BigInt (the BigInt default int).
    const int8_t a8 = 127;
    const uint16_t u16 = 60000;
    check(tpy::add_check<BigInt>(a8, a8) == BigInt(254), "BigInt add of int8");
    check(tpy::sub_check<BigInt>(int8_t{-128}, a8) == BigInt(-255), "BigInt sub of int8");
    check(tpy::mul_check<BigInt>(u16, u16) == BigInt(int64_t{3600000000}),
          "BigInt mul of uint16 does not overflow");
    check(tpy::div_check<BigInt>(int8_t{-7}, int8_t{2}) == BigInt(-4), "BigInt floor div");
    check(tpy::mod_check<BigInt>(int8_t{-7}, int8_t{2}) == BigInt(1), "BigInt floor mod");
    check(tpy::neg_check<BigInt>(uint8_t{200}) == BigInt(-200), "BigInt negation of uint8");
    check(tpy::pow_check<BigInt>(uint16_t{60000}, uint16_t{3}) == BigInt(60000) * BigInt(60000) * BigInt(60000),
          "BigInt power of uint16");
    check(tpy::lshift_check<BigInt>(int8_t{1}, int64_t{70}) == (BigInt(1) << 70),
          "BigInt left shift past 64 bits");
    check(tpy::lshift_check<BigInt>(uint8_t{3}, BigInt(4)) == BigInt(48),
          "BigInt left shift by a BigInt count");
    check(raises_overflow([] { (void)tpy::lshift_check<BigInt>(int8_t{1}, int64_t{1} << 40); }),
          "a BigInt shift count past int32 raises OverflowError, never truncates");
    bool zero_div = false;
    try {
        (void)tpy::div_check<BigInt>(int8_t{1}, int8_t{0});
    } catch (const tpy::ZeroDivisionError&) {
        zero_div = true;
    }
    check(zero_div, "BigInt division by zero raises ZeroDivisionError");

    // Integers of different types compare by value.
    check(!tpy::key_eq(uint32_t{4294967295u}, int32_t{-1}),
          "key_eq: uint32 max is not int32 -1");
    check(tpy::key_eq(int8_t{44}, int32_t{44}), "key_eq: equal values across widths");
    std::vector<uint32_t> xs32 = {4294967295u};
    check(!tpy::seq_contains(xs32, int32_t{-1}), "seq_contains: -1 not in [uint32 max]");
    std::vector<int8_t> xs8 = {44};
    check(!tpy::seq_contains(xs8, int32_t{300}), "seq_contains: 300 not in [44]");
    check(tpy::seq_contains(xs8, int32_t{44}), "seq_contains: 44 in [44]");

    tpy::ordered_set<int8_t> s8;
    s8.insert(int8_t{44});
    check(!s8.contains(int32_t{300}), "ordered_set: 300 is absent, not a wrapped 44");
    check(s8.contains(int32_t{44}), "ordered_set: 44 found through an int32 key");
    check(!s8.erase(int32_t{300}), "ordered_set: erase of 300 removes nothing");
    check(s8.contains(int8_t{44}), "ordered_set: 44 survives the erase of 300");

    tpy::ordered_map<int8_t, int32_t> m8;
    m8.insert_or_assign(int8_t{44}, 1);
    check(!m8.contains(int32_t{300}), "ordered_map: 300 is absent, not a wrapped 44");
    check(m8.find(int32_t{300}) == m8.items_end(), "ordered_map: find(300) misses");
    check(!m8.erase(int32_t{300}), "ordered_map: erase of 300 removes nothing");
    check(m8.contains(int32_t{44}), "ordered_map: 44 found through an int32 key");

    if (failures == 0) std::printf("ok\n");
    return failures == 0 ? 0 : 1;
}
