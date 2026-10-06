/**
 * dict / set update across element widths self-check.
 *
 * An inferred dict or set literal decides its key, value and element widths
 * per container, so `d.update(other)` and `s |= other` may meet a source
 * whose leaves were decided narrower than the target's (an `other` that
 * settled at int32, stored into a dict another store widened to int64). The
 * update natives take the source at its own instantiation and convert each
 * entry as it goes in, as `list_extend` converts elements -- losslessly
 * only (`widens_to`): a narrower target, a float into an int, an explicit
 * constructor or a container into another container does not instantiate.
 * No generated case reaches every spelling with a same-width twin beside it,
 * so both are pinned here, the refused instantiations as static asserts.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <cstdint>
#include <cstdio>
#include <string>
#include <string_view>
#include <tuple>
#include <vector>

#include "tpy/tpy.hpp"

namespace {

template<typename M, typename O>
concept dict_updatable = requires(M& m, const O& o) { ::tpy::dict_update(m, o); };
template<typename S, typename O>
concept set_updatable = requires(S& s, const O& o) { ::tpy::set_update(s, o); };
template<typename S, typename O>
concept set_symdiff_updatable = requires(S& s, const O& o) {
    ::tpy::set_symmetric_difference_update(s, o);
};

template<typename K, typename V>
using Map = ::tpy::ordered_map<K, V>;
template<typename T>
using Set = ::tpy::ordered_set<T>;

// Lossless conversions instantiate.
static_assert(dict_updatable<Map<std::string, int64_t>, Map<std::string, int32_t>>);
static_assert(dict_updatable<Map<int64_t, double>, Map<int32_t, float>>);
static_assert(dict_updatable<Map<int64_t, int32_t>, Map<uint32_t, int32_t>>);
static_assert(dict_updatable<Map<std::tuple<int64_t, int32_t>, int32_t>,
                             Map<std::tuple<int32_t, int32_t>, int32_t>>);
static_assert(set_updatable<Set<int64_t>, Set<int8_t>>);
static_assert(set_symdiff_updatable<Set<int64_t>, Set<int32_t>>);

// A narrowing, a float into an int, an int into a float, a sign change, an
// explicit constructor or a container into another container does not.
static_assert(!dict_updatable<Map<std::string, int32_t>, Map<std::string, int64_t>>);
static_assert(!dict_updatable<Map<int32_t, int32_t>, Map<int64_t, int32_t>>);
static_assert(!dict_updatable<Map<std::string, int64_t>, Map<std::string, double>>);
static_assert(!dict_updatable<Map<std::string, double>, Map<std::string, int32_t>>);
static_assert(!dict_updatable<Map<std::string, uint32_t>, Map<std::string, int32_t>>);
static_assert(!dict_updatable<Map<std::string, int32_t>, Map<std::string, uint32_t>>);
static_assert(!dict_updatable<Map<std::string, std::string>,
                              Map<std::string, std::string_view>>);
static_assert(!dict_updatable<Map<std::string, std::vector<int64_t>>,
                              Map<std::string, std::vector<int32_t>>>);
static_assert(!dict_updatable<Map<std::tuple<int32_t, int32_t>, int32_t>,
                              Map<std::tuple<int64_t, int32_t>, int32_t>>);
static_assert(!set_updatable<Set<int32_t>, Set<int64_t>>);
static_assert(!set_updatable<Set<float>, Set<double>>);
static_assert(!set_symdiff_updatable<Set<int16_t>, Set<int32_t>>);

int failures = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

}  // namespace

int main() {
    // A narrower value source widens into the target's value type.
    ::tpy::ordered_map<std::string, int64_t> d;
    d.insert_or_assign(std::string("a"), int64_t{1099511627776});
    ::tpy::ordered_map<std::string, int32_t> narrow;
    narrow.insert_or_assign(std::string("b"), int32_t{7});
    narrow.insert_or_assign(std::string("a"), int32_t{-3});
    ::tpy::dict_update(d, narrow);
    check(d.size() == 2, "dict_update: a narrower source adds its new key");
    check(*::tpy::dict_get(d, "a") == -3,
          "dict_update: a narrower source replaces an existing value");
    check(*::tpy::dict_get(d, "b") == 7,
          "dict_update: a narrower value converts");

    // A narrower key converts too; insertion order follows the source.
    ::tpy::ordered_map<int64_t, std::string> k;
    k.insert_or_assign(int64_t{1099511627776}, std::string("big"));
    ::tpy::ordered_map<int32_t, std::string> narrow_keys;
    narrow_keys.insert_or_assign(int32_t{5}, std::string("five"));
    ::tpy::dict_update(k, narrow_keys);
    check(k.size() == 2 && *::tpy::dict_get(k, int64_t{5}) == "five",
          "dict_update: a narrower key converts");

    // The same-width form is unchanged: entries are copied, the source kept.
    ::tpy::ordered_map<std::string, int64_t> same;
    same.insert_or_assign(std::string("c"), int64_t{2});
    ::tpy::dict_update(d, same);
    check(d.size() == 3 && same.size() == 1,
          "dict_update: a same-width source is copied in and kept");

    // Tuple keys convert member by member.
    ::tpy::ordered_map<std::tuple<int64_t, int32_t>, int32_t> t;
    ::tpy::ordered_map<std::tuple<int32_t, int32_t>, int32_t> narrow_t;
    narrow_t.insert_or_assign(std::tuple<int32_t, int32_t>{1, 2}, int32_t{3});
    ::tpy::dict_update(t, narrow_t);
    check(t.size() == 1, "dict_update: a narrower tuple key converts");
    check(::tpy::dict_get(t, std::tuple<int64_t, int32_t>{1, 2}) != nullptr
              && *::tpy::dict_get(t, std::tuple<int64_t, int32_t>{1, 2}) == 3,
          "dict_update: a converted tuple key is found by its new type");

    // Sets: update and symmetric difference from a narrower source.
    ::tpy::ordered_set<int64_t> s;
    s.insert(int64_t{1099511627776});
    s.insert(int64_t{1});
    ::tpy::ordered_set<int32_t> narrow_s;
    narrow_s.insert(int32_t{1});
    narrow_s.insert(int32_t{7});
    ::tpy::set_update(s, narrow_s);
    check(s.size() == 3 && s.contains(int64_t{7}),
          "set_update: a narrower source converts");
    ::tpy::set_symmetric_difference_update(s, narrow_s);
    check(s.size() == 1 && s.contains(int64_t{1099511627776}),
          "set_symmetric_difference_update: a narrower source converts");

    // The same-width self-update keeps its aliasing guard.
    ::tpy::ordered_set<int64_t> self_s;
    self_s.insert(int64_t{4});
    ::tpy::set_symmetric_difference_update(self_s, self_s);
    check(self_s.empty(), "set_symmetric_difference_update: s ^= s empties s");

    if (failures == 0) std::printf("ok\n");
    return failures == 0 ? 0 : 1;
}
