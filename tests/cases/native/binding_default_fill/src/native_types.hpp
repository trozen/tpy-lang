#pragma once
#include <cstdint>
#include <functional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

// Every C++ default below differs from the TPy one, so an omitted argument
// that reaches C++ instead of the TPy default shows in the output.
namespace nb {
enum class E : int { A = 1, B = 2 };
struct Ent {
    E e; int64_t p; int32_t n;
    Ent(E e_, int64_t p_, int32_t n_) : e(e_), p(p_), n(n_) {}
};
// Variadic: no C++ default reaches an omitted argument.
struct Q {
    std::vector<Ent> items;
    template<class... A> void emplace_back(A&&... a) { items.emplace_back(std::forward<A>(a)...); }
    int64_t last_p() const { return items.back().p; }
    int32_t last_n() const { return items.back().n; }
};
inline int32_t cppdef(int32_t x, int32_t y = 100) { return x * 1000 + y; }
inline int32_t limited(int32_t x, int32_t y = 100) { return x * 1000 + y; }
inline int32_t limited_cast(int32_t x, int32_t y = 100) { return x * 1000 + y; }
inline int32_t tagged(int32_t x, std::string_view t = "cpp default") { return x * 100 + (int32_t)t.size(); }
inline int32_t moded(int32_t x, E e = E::A) { return x * 10 + (int32_t)e; }
template<class B> int32_t sized(int32_t x, const B& d) { return x * 100 + (int32_t)d.size(); }
template<class T> T generic(T x, T y = T{9}) { return x + y; }
inline int32_t add2(int32_t a, int32_t b) { return a * 1000 + b; }
struct RD {
    int32_t v;
    explicit RD(int32_t b, int32_t k = 100) : v(b * 1000 + k) {}
    int32_t get(int32_t n = 100) const { return v + n; }
};
inline int32_t ff(const RD& r, int32_t n = 100) { return r.v + n * 10; }
template<class T> struct GBox {
    T v;
    GBox(T x, T y = T{9}) : v(x * 1000 + y) {}
    T get() const { return v; }
};
struct GM {
    template<class T> T pick(T x, T y = T{9}) const { return x * 1000 + y; }
};
struct DM {
    int32_t f(int32_t x, int32_t k = 100) const { return x * 1000 + k; }
    int32_t f(std::string_view s, int32_t k = 100) const { return (int32_t)s.size() * 1000 + k; }
};
struct OV {
    int32_t f(int32_t x = 100) const { return x; }
    int32_t f(std::string_view s) const { return (int32_t)s.size(); }
};
template<class T> struct PBag {
    int32_t k; T last{};
    explicit PBag(int32_t kk = 100) : k(kk) {}
    void push(T v) { last = v; }
    int32_t tag(int32_t n = 100) const { return k * 1000 + n; }
};
// A native exception with a constructor group: the call renders as written.
struct GErr : ::tpy::Exception {
    GErr(int32_t a, int32_t b) : ::tpy::Exception(std::to_string(a * 1000 + b)) {}
    GErr(std::string_view s, int32_t k) : ::tpy::Exception(std::string(s) + std::to_string(k)) {}
    TPY_THROWABLE_VIRTUALS(GErr)
};
// A constructor group taking a callable: a keyword lambda takes the
// candidate's callable parameter as its hint.
struct LErr : ::tpy::Exception {
    LErr(std::function<int32_t(int32_t)> f) : ::tpy::Exception(std::to_string(f(3))) {}
    LErr(int32_t n) : ::tpy::Exception(std::to_string(n)) {}
    TPY_THROWABLE_VIRTUALS(LErr)
};
// A constructor group with a keyword-only parameter and a generic variant.
struct KErr : ::tpy::Exception {
    KErr(std::string_view s, int32_t k) : ::tpy::Exception(std::string(s) + ":" + std::to_string(k)) {}
    KErr(const std::vector<int32_t>& xs, int32_t k) : ::tpy::Exception(std::to_string(xs.size()) + ":" + std::to_string(k)) {}
    TPY_THROWABLE_VIRTUALS(KErr)
};
struct VErr : ::tpy::Exception {
    VErr(std::function<int32_t(int32_t)>) : ::tpy::Exception("callable") {}
    VErr(int32_t) : ::tpy::Exception("number") {}
    TPY_THROWABLE_VIRTUALS(VErr)
};
template<class V> int32_t nf(int32_t a, V rest, int32_t k = 100) {
    for (auto x : rest) a += x;
    return a * 1000 + k;
}
template<class V> int32_t total(V xs, int32_t k = 100) {
    int32_t s = 0;
    for (auto x : xs) s += x;
    return s * 100 + k;
}
inline int32_t w(int32_t x, int32_t k = 100) { return x * 1000 + k; }
template<class T> T add3(T x, T y = T{8}, T z = T{9}) { return x * 100 + y * 10 + z; }
inline std::string joined(std::string_view x, std::string_view y = "cpp") {
    return std::string(x) + "/" + std::string(y);
}
template<class T> struct GEx : ::tpy::Exception {
    GEx(T v, int32_t k = 100) : ::tpy::Exception(std::to_string(v * 1000 + k)) {}
    TPY_THROWABLE_VIRTUALS(GEx)
};
}
extern "C" int32_t nb_cfn(int32_t x, int32_t y);
