// Small-result integer operators must avoid limb vectors and retain inline storage.
// operator new counts vector allocations; direct malloc is covered by path review.
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <new>

#include "tpy/bigint.hpp"

namespace {
bool measuring = false;
size_t new_calls = 0;
int failures = 0;
constexpr int64_t small_min = -(int64_t{1} << 62);
constexpr int64_t small_max = (int64_t{1} << 62) - 1;

void check(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

bool fits_small(int64_t n) { return n >= small_min && n <= small_max; }

template<class F>
auto evaluate(const char* what, bool noalloc, F operation) {
    new_calls = 0;
    measuring = true;
    auto result = operation();
    measuring = false;
    if (noalloc) check(new_calls == 0, what);
    return result;
}

void integer(const tpy::BigInt& result, int64_t expected, const char* what) {
    check(result.to_fixed_check<int64_t>() == expected, what);
    check(result.is_small() == fits_small(expected), "canonical integer storage");
}

template<class E, class F>
void raises(F operation, const char* message) {
    try {
        operation();
        check(false, "missing exception");
    } catch (const E& ex) {
        check(std::string_view(ex.what()) == message, "exception message");
    } catch (...) {
        check(false, "wrong exception type");
    }
}

void pair(int64_t av, int64_t bv) {
    const tpy::BigInt a(av), b(bv);
    const bool small = a.is_small() && b.is_small();
    check(evaluate("== alloc", small, [&] { return a == b; }) == (av == bv), "==");
    check(evaluate("!= alloc", small, [&] { return a != b; }) == (av != bv), "!=");
    check(evaluate("< alloc", small, [&] { return a < b; }) == (av < bv), "<");
    check(evaluate("<= alloc", small, [&] { return a <= b; }) == (av <= bv), "<=");
    check(evaluate("> alloc", small, [&] { return a > b; }) == (av > bv), ">");
    check(evaluate(">= alloc", small, [&] { return a >= b; }) == (av >= bv), ">=");
    integer(evaluate("& alloc", small, [&] { return a & b; }), av & bv, "&");
    integer(evaluate("| alloc", small, [&] { return a | b; }), av | bv, "|");
    integer(evaluate("^ alloc", small, [&] { return a ^ b; }), av ^ bv, "^");
    if (bv == 0) {
        raises<tpy::ZeroDivisionError>([&] { (void)(a / b); },
                                      "integer division or modulo by zero");
        raises<tpy::ZeroDivisionError>([&] { (void)(a % b); },
                                      "integer modulo by zero");
        raises<tpy::ZeroDivisionError>([&] { (void)a.floor_divmod(b); },
                                      "integer division or modulo by zero");
        return;
    }

    // Derive the oracle independently of the runtime's fixed_int helpers.
    int64_t q = av / bv;
    int64_t r = av % bv;
    if (r != 0 && ((av < 0) != (bv < 0))) {
        --q;
        r += bv;
    }
    integer(evaluate("// alloc", small && fits_small(q), [&] { return a / b; }), q, "//");
    integer(evaluate("% alloc", small, [&] { return a % b; }), r, "%");
    auto qr = evaluate("divmod alloc", small && fits_small(q), [&] {
        return a.floor_divmod(b);
    });
    integer(std::get<0>(qr), q, "divmod quotient");
    integer(std::get<1>(qr), r, "divmod remainder");
    auto observed_q = std::get<0>(qr).to_fixed_check<int64_t>();
    auto observed_r = std::get<1>(qr).to_fixed_check<int64_t>();
    check(static_cast<__int128>(observed_q) * bv + observed_r == av, "q*b+r == a");
    check(std::abs(observed_r) < std::abs(bv), "remainder magnitude");
    check(observed_r == 0 || (observed_r < 0) == (bv < 0), "remainder sign");
}

void shifts(int64_t av) {
    const tpy::BigInt a(av);
    volatile int32_t counts[] = {-1, 0, 1, 61, 62, 63, 64, 200, INT32_MAX};
    for (int32_t count : counts) {
        const tpy::BigInt big_count(count);
        if (count < 0) {
            raises<tpy::ValueError>([&] { (void)(a << count); }, "negative shift count");
            raises<tpy::ValueError>([&] { (void)(a >> count); }, "negative shift count");
            raises<tpy::ValueError>([&] { (void)(a << big_count); }, "negative shift count");
            raises<tpy::ValueError>([&] { (void)(a >> big_count); }, "negative shift count");
            continue;
        }
        int64_t expected = count >= 63 ? (av < 0 ? -1 : 0) : av >> count;
        // Avoid the deliberately unchanged heap-input huge-divisor fallback.
        if (count != INT32_MAX || a.is_small()) {
            integer(evaluate(">> alloc", a.is_small(), [&] { return a >> count; }),
                    expected, ">>");
            integer(evaluate(">> BigInt alloc", a.is_small(), [&] { return a >> big_count; }),
                    expected, ">> BigInt");
        }
        if (count == INT32_MAX) continue;
        __int128 wide = count < 63 ? static_cast<__int128>(av) * (int64_t{1} << count) : 0;
        bool inline_result = av == 0 || (count < 63 && wide >= small_min && wide <= small_max);
        auto shifted = evaluate("<< alloc", a.is_small() && inline_result, [&] {
            return a << count;
        });
        auto big_shifted = evaluate("<< BigInt alloc", a.is_small() && inline_result, [&] {
            return a << big_count;
        });
        check(shifted.is_small() == inline_result, "<< storage");
        check(big_shifted == shifted, "shift-count overload agreement");
        if (inline_result) integer(shifted, static_cast<int64_t>(wide), "<< inline value");
        check((shifted >> count) == a, "left shift inverse");
        check((shifted < tpy::BigInt(0)) == (av < 0), "left shift sign");
    }
}

void baselines(int64_t av) {
    const tpy::BigInt a(av), zero(0), one(1), minus_one(-1);
    integer(evaluate("+ alloc", a.is_small(), [&] { return a + zero; }), av, "+");
    integer(evaluate("- alloc", a.is_small(), [&] { return a - zero; }), av, "-");
    integer(evaluate("* alloc", a.is_small(), [&] { return a * one; }), av, "*");
    integer(evaluate("~ alloc", a.is_small(), [&] { return ~a; }), ~av, "~");
    if (a.is_small()) {
        auto compound = evaluate("compound alloc", true, [&] {
            auto x = a;
            x /= one;
            x %= minus_one;
            x <<= 1;
            x >>= 1;
            x |= a;
            x &= minus_one;
            x ^= zero;
            return x;
        });
        integer(compound, av, "compound operators");
    }
}

void true_division() {
    volatile int64_t inputs[] = {0, 17, -17, 3, -3, (int64_t{1} << 53) - 1};
    for (int64_t av : inputs) {
        for (int64_t bv : inputs) {
            const tpy::BigInt a(av), b(bv);
            if (bv == 0) {
                raises<tpy::ZeroDivisionError>([&] { (void)tpy::truediv(a, b); },
                                              "division by zero");
                continue;
            }
            double expected = static_cast<double>(av) / static_cast<double>(bv);
            double actual = evaluate("true division existing fast path", true, [&] {
                return tpy::truediv(a, b);
            });
            check(actual == expected, "true division value");
            check(std::signbit(actual) == std::signbit(expected), "true division signed zero");
        }
    }
    volatile int64_t numerator = (int64_t{1} << 60) + (int64_t{1} << 7);
    const tpy::BigInt a(static_cast<int64_t>(numerator)), three(3);
    check(tpy::truediv(a, three) == 3.843071682022824e17, "true division exact rounding");
    const tpy::BigInt halfway((int64_t{1} << 53) + 1);
    check(tpy::truediv(halfway, tpy::BigInt(1)) == 9007199254740992.0,
          "true division ties to even");
}
} // namespace

void* operator new(size_t size) {
    if (measuring) ++new_calls;
    if (void* p = std::malloc(size ? size : 1)) return p;
    throw std::bad_alloc();
}
void* operator new[](size_t size) { return ::operator new(size); }
void operator delete(void* p) noexcept { std::free(p); }
void operator delete(void* p, size_t) noexcept { std::free(p); }
void operator delete[](void* p) noexcept { std::free(p); }
void operator delete[](void* p, size_t) noexcept { std::free(p); }

int main() {
    // Volatile reads keep the optimized selfcheck's operands runtime-dependent.
    volatile int64_t values[] = {
        0, 1, -1, 2, -2, 17, -17, small_min, small_min + 1,
        small_max - 1, small_max, small_min - 1, small_max + 1,
    };
    for (int64_t a : values) {
        baselines(a);
        shifts(a);
        for (int64_t b : values) pair(a, b);
    }
    true_division();
    return failures ? 1 : 0;
}
