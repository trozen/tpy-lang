// Pin Python exceptions independently of generated bindings and libm errno.
#include "tpy/stdlib/math.hpp"

#include <array>
#include <cmath>
#include <cstdio>
#include <limits>

namespace {
namespace math = tpy::stdlib::math;
constexpr double inf = std::numeric_limits<double>::infinity();
constexpr double nan = std::numeric_limits<double>::quiet_NaN();
constexpr double tiny = std::numeric_limits<double>::denorm_min();
constexpr double maximum = std::numeric_limits<double>::max();
int failures = 0;

// F = finite nonzero, Z/z = positive/negative zero, I/i = +/-infinity.
// N = NaN, V/O = ValueError/OverflowError.
char kind(double value) {
    if (std::isnan(value)) return 'N';
    if (std::isinf(value)) return std::signbit(value) ? 'i' : 'I';
    if (value == 0.0) return std::signbit(value) ? 'z' : 'Z';
    return 'F';
}

template<class F>
void expect(const char* name, F operation, char wanted, double value = nan,
            double x = nan, double y = nan, double z = nan) {
    char actual;
    double result = nan;
    try {
        result = operation();
        actual = kind(result);
    } catch (const tpy::ValueError&) {
        actual = 'V';
    } catch (const tpy::OverflowError&) {
        actual = 'O';
    } catch (...) {
        actual = '?';
    }
    if (actual != wanted || (wanted == 'F' && !std::isnan(value) && result != value)) {
        std::printf("FAIL: %s(%a, %a, %a): expected %c %a, got %c %a\n",
                    name, x, y, z, wanted, value, actual, result);
        ++failures;
    }
}

void unary_controls() {
    struct Control {
        const char* name;
        double (*operation)(double);
        double x;
        double value;
    };
    const Control controls[] = {
        {"log", math::checked_log, 1.0, 0.0},
        {"log10", math::checked_log10, 100.0, 2.0},
        {"log2", math::checked_log2, 8.0, 3.0},
        {"log1p", math::checked_log1p, -0.0, -0.0},
        {"sqrt", math::checked_sqrt, 4.0, 2.0},
        {"asin", math::checked_asin, -0.0, -0.0},
        {"acos", math::checked_acos, 1.0, 0.0},
        {"acosh", math::checked_acosh, 1.0, 0.0},
        {"atanh", math::checked_atanh, -0.0, -0.0},
        {"sin", math::checked_sin, -0.0, -0.0},
        {"cos", math::checked_cos, 0.0, 1.0},
        {"tan", math::checked_tan, -0.0, -0.0},
        {"exp", math::checked_exp, 0.0, 1.0},
        {"exp2", math::checked_exp2, 3.0, 8.0},
        {"expm1", math::checked_expm1, -0.0, -0.0},
        {"sinh", math::checked_sinh, -0.0, -0.0},
        {"cosh", math::checked_cosh, 0.0, 1.0},
        {"gamma", math::checked_gamma, 3.0, 2.0},
        {"lgamma", math::checked_lgamma, 1.0, 0.0},
    };
    for (const auto& c : controls) {
        expect(c.name, [&] { return c.operation(c.x); }, kind(c.value), c.value, c.x);
        expect(c.name, [&] { return c.operation(nan); }, 'N', nan, nan);
    }
    const Control domains[] = {
        {"log", math::checked_log, 0.0, 0.0},
        {"log10", math::checked_log10, -1.0, 0.0},
        {"log2", math::checked_log2, -0.0, 0.0},
        {"log1p", math::checked_log1p, -1.0, 0.0},
        {"sqrt", math::checked_sqrt, -1.0, 0.0},
        {"asin", math::checked_asin, 2.0, 0.0},
        {"acos", math::checked_acos, -2.0, 0.0},
        {"acosh", math::checked_acosh, 0.0, 0.0},
        {"atanh", math::checked_atanh, 1.0, 0.0},
        {"sin", math::checked_sin, inf, 0.0},
        {"cos", math::checked_cos, -inf, 0.0},
        {"tan", math::checked_tan, inf, 0.0},
    };
    for (const auto& c : domains)
        expect(c.name, [&] { return c.operation(c.x); }, 'V', nan, c.x);

    const Control ranges[] = {
        {"exp", math::checked_exp, 1000.0, 0.0},
        {"exp2", math::checked_exp2, 1024.0, 0.0},
        {"expm1", math::checked_expm1, 1000.0, 0.0},
        {"sinh", math::checked_sinh, -1000.0, 0.0},
        {"cosh", math::checked_cosh, -1000.0, 0.0},
    };
    for (const auto& c : ranges) {
        expect(c.name, [&] { return c.operation(c.x); }, 'O', nan, c.x);
        expect(c.name, [&] { return c.operation(inf); }, 'I', nan, inf);
    }
    expect("exp underflow", [] { return math::checked_exp(-1000.0); }, 'Z');
    expect("exp2 underflow", [] { return math::checked_exp2(-1075.0); }, 'Z');
    expect("expm1 -infinity", [] { return math::checked_expm1(-inf); }, 'F', -1.0);
    expect("sqrt -zero", [] { return math::checked_sqrt(-0.0); }, 'z');
    expect("sqrt infinity", [] { return math::checked_sqrt(inf); }, 'I');
    expect("sinh -infinity", [] { return math::checked_sinh(-inf); }, 'i');
    expect("cosh -infinity", [] { return math::checked_cosh(-inf); }, 'I');
}

void power_matrix() {
    const double bases[] = {nan, -inf, -2.0, -1.0, -0.0, 0.0, 0.5, 1.0, 2.0, inf};
    const double powers[] = {nan, -inf, -3.0, -0.5, -0.0, 0.0, 0.5, 2.0, 3.0, inf};
    // CPython outcomes; keeping these literal prevents an oracle from copying
    // the adapter's branching, especially for NaNs and infinite exponents.
    const char* outcomes[] = {
        "NNNNFFNNNN", "NZzZFFIIiI", "NZFVFFVFFI", "NFFVFFVFFF",
        "NIVVFFZZzZ", "NIVVFFZZZZ", "NIFFFFFFFZ", "FFFFFFFFFF",
        "NZFFFFFFFI", "NZZZFFIIII",
    };
    for (unsigned i = 0; i < std::size(bases); ++i) {
        for (unsigned j = 0; j < std::size(powers); ++j) {
            const double x = bases[i], y = powers[j];
            expect("pow", [&] { return math::checked_pow(x, y); }, outcomes[i][j],
                   outcomes[i][j] == 'F' ? std::pow(x, y) : nan, x, y);
        }
    }
    expect("pow control", [] { return math::checked_pow(2.0, 3.0); }, 'F', 8.0);
    expect("pow overflow", [] { return math::checked_pow(maximum, 2.0); }, 'O');
    expect("pow negative overflow", [] { return math::checked_pow(-maximum, 3.0); }, 'O');
    expect("pow underflow", [] { return math::checked_pow(-1e-200, 3.0); }, 'z');
}

void remainder_matrices() {
    const double values[] = {nan, -inf, -2.0, -0.0, 0.0, 2.0, inf};
    const char* outcomes[] = {
        "NNNNNNN", "NVVVVVV", "NFzVVzF", "NzzVVzz",
        "NZZVVZZ", "NFZVVZF", "NVVVVVV",
    };
    struct Operation { const char* name; double (*checked)(double, double);
                       double (*raw)(double, double); };
    const Operation operations[] = {
        {"fmod", math::checked_fmod, std::fmod},
        {"remainder", math::checked_remainder, std::remainder},
    };
    for (const auto& op : operations) {
        for (unsigned i = 0; i < std::size(values); ++i) {
            for (unsigned j = 0; j < std::size(values); ++j) {
                const double x = values[i], y = values[j];
                const char wanted = outcomes[i][j];
                expect(op.name, [&] { return op.checked(x, y); }, wanted,
                       wanted == 'F' ? op.raw(x, y) : nan, x, y);
            }
        }
    }
    expect("fmod control", [] { return math::checked_fmod(7.0, 4.0); }, 'F', 3.0);
    expect("remainder ties even", [] { return math::checked_remainder(6.0, 4.0); }, 'F', -2.0);
    expect("remainder control", [] { return math::checked_remainder(7.0, 4.0); }, 'F', -1.0);
}

void gamma_tables() {
    const double values[] = {nan, -inf, -200.5, -3.0, -0.5, -tiny, -0.0,
                             0.0, tiny, 0.5, 1.0, 2.0, 172.0, maximum, inf};
    const char* gamma = "NVzVFOVVOFFFOOI";
    const char* lgamma = "NIFVFFVVFFZZFOI";
    for (unsigned i = 0; i < std::size(values); ++i) {
        const double x = values[i];
        expect("gamma", [&] { return math::checked_gamma(x); }, gamma[i], nan, x);
        expect("lgamma", [&] { return math::checked_lgamma(x); }, lgamma[i], nan, x);
    }
}

void ldexp_matrix() {
    const double values[] = {nan, -inf, -1.0, -0.0, 0.0, tiny, 1.0, maximum, inf};
    const int powers[] = {std::numeric_limits<int>::min(), -1075, 0, 1024,
                          std::numeric_limits<int>::max()};
    const char* outcomes[] = {
        "NNNNN", "iiiii", "zzFOO", "zzzzz", "ZZZZZ",
        "ZZFFO", "ZZFOO", "ZFFOO", "IIIII",
    };
    for (unsigned i = 0; i < std::size(values); ++i) {
        for (unsigned j = 0; j < std::size(powers); ++j) {
            const double x = values[i];
            const int n = powers[j];
            const char wanted = outcomes[i][j];
            expect("ldexp", [&] { return math::checked_ldexp(x, n); }, wanted,
                   wanted == 'F' ? std::ldexp(x, n) : nan, x, n);
        }
    }
    expect("ldexp control", [] { return math::checked_ldexp(1.5, 3); }, 'F', 12.0);
}

void fma_matrix() {
    const double values[] = {nan, -inf, -1.0, -0.0, 0.0, 1.0, inf};
    // CPython 3.13 policy, with z indexing each string. Literal outcomes keep
    // the oracle independent of the adapter's classification predicates.
    const char* outcomes[][7] = {
        {"NNNNNNN", "NNNNNNN", "NNNNNNN", "NNNNNNN", "NNNNNNN", "NNNNNNN", "NNNNNNN"},
        {"NNNNNNN", "NVIIIII", "NVIIIII", "NVVVVVV", "NVVVVVV", "NiiiiiV", "NiiiiiV"},
        {"NNNNNNN", "NVIIIII", "NiZFFFI", "NiFZZFI", "NiFzZFI", "NiFFFZI", "NiiiiiV"},
        {"NNNNNNN", "NVVVVVV", "NiFZZFI", "NiFZZFI", "NiFzZFI", "NiFzZFI", "NVVVVVV"},
        {"NNNNNNN", "NVVVVVV", "NiFzZFI", "NiFzZFI", "NiFZZFI", "NiFZZFI", "NVVVVVV"},
        {"NNNNNNN", "NiiiiiV", "NiFFFZI", "NiFzZFI", "NiFZZFI", "NiZFFFI", "NVIIIII"},
        {"NNNNNNN", "NiiiiiV", "NiiiiiV", "NVVVVVV", "NVVVVVV", "NVIIIII", "NVIIIII"},
    };
    for (unsigned i = 0; i < std::size(values); ++i) {
        for (unsigned j = 0; j < std::size(values); ++j) {
            for (unsigned k = 0; k < std::size(values); ++k) {
                const double x = values[i], y = values[j], z = values[k];
                const char wanted = outcomes[i][j][k];
                expect("fma", [&] { return math::checked_fma(x, y, z); }, wanted,
                       wanted == 'F' ? x * y + z : nan, x, y, z);
            }
        }
    }
    expect("fma control", [] { return math::checked_fma(2.0, 3.0, 4.0); }, 'F', 10.0);
    expect("fma fused overflow cancellation", [] { return math::checked_fma(maximum, 2.0, -maximum); }, 'F', maximum);
    expect("fma fused low bits", [] { return math::checked_fma(0x1.0000002p0, 0x1.ffffffcp-1, -1.0); }, 'F', -0x1p-54);
    expect("fma underflow", [] { return math::checked_fma(-tiny, 0.5, -0.0); }, 'z');
    expect("fma invalid", [] { return math::checked_fma(0.0, inf, 1.0); }, 'V');
    expect("fma opposed infinities", [] { return math::checked_fma(inf, 1.0, -inf); }, 'V');
    expect("fma finite overflow", [] { return math::checked_fma(maximum, 2.0, 0.0); }, 'O');
    expect("fma negative overflow", [] { return math::checked_fma(-maximum, 2.0, 0.0); }, 'O');
    expect("fma legitimate infinity", [] { return math::checked_fma(inf, 2.0, inf); }, 'I');
    expect("fma legitimate negative infinity", [] { return math::checked_fma(-inf, 2.0, -inf); }, 'i');
    expect("fma negative zero", [] { return math::checked_fma(-0.0, 2.0, -0.0); }, 'z');
    expect("fma positive zero", [] { return math::checked_fma(-0.0, 2.0, 0.0); }, 'Z');
    expect("fma first nan", [] { return math::checked_fma(nan, inf, -inf); }, 'N');
    expect("fma second nan", [] { return math::checked_fma(inf, nan, -inf); }, 'N');
    expect("fma nan precedence", [] { return math::checked_fma(0.0, inf, nan); }, 'N');
    expect("fma nan precedence swapped", [] { return math::checked_fma(inf, 0.0, nan); }, 'N');
}

void ulp_edges() {
    const double spacing = 0x1p971;
    expect("ulp max", [] { return math::ulp(maximum); }, 'F', spacing);
    expect("ulp negative max", [] { return math::ulp(-maximum); }, 'F', spacing);
    expect("ulp predecessor", [] { return math::ulp(std::nextafter(maximum, 0.0)); }, 'F', spacing);
    expect("ulp zero", [] { return math::ulp(0.0); }, 'F', tiny);
    expect("ulp negative zero", [] { return math::ulp(-0.0); }, 'F', tiny);
    expect("ulp subnormal", [] { return math::ulp(tiny); }, 'F', tiny);
    expect("ulp positive infinity", [] { return math::ulp(inf); }, 'I');
    expect("ulp negative infinity", [] { return math::ulp(-inf); }, 'I');
    expect("ulp nan", [] { return math::ulp(nan); }, 'N');
}
}  // namespace

int main() {
    unary_controls();
    power_matrix();
    remainder_matrices();
    gamma_tables();
    ldexp_matrix();
    fma_matrix();
    ulp_edges();
    return failures == 0 ? 0 : 1;
}
