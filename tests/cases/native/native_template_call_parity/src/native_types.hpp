#pragma once
#include <cstdint>
#include <string>
#include <string_view>
#include <tuple>
#include <vector>

namespace nb {
struct Item {
    int32_t n_;
    explicit Item(int32_t n) : n_(n) {}
};

// A str-keyed lookup: the key arrives as the view a `str` parameter is.
struct Table {
    std::vector<std::string> keys{"a", "b"};
    std::vector<int32_t> vals{10, 20};
    int32_t at(std::string_view k) const {
        for (std::size_t i = 0; i < keys.size(); ++i)
            if (keys[i] == k) return vals[i];
        return -1;
    }
};

// A nullable element lookup: the pointer a borrowed `Item | None` is.
struct Bag {
    std::vector<Item> items{Item(1), Item(2)};
    Item* find(int32_t i) {
        return (i >= 0 && static_cast<std::size_t>(i) < items.size())
            ? &items[static_cast<std::size_t>(i)] : nullptr;
    }
    const Item* find(int32_t i) const {
        return (i >= 0 && static_cast<std::size_t>(i) < items.size())
            ? &items[static_cast<std::size_t>(i)] : nullptr;
    }
    int32_t n_at(int32_t i) const { return items[static_cast<std::size_t>(i)].n_; }
};

struct Blobs {
    std::vector<std::uint8_t> data{65, 66, 67};
};

struct Pairs {
    int32_t a = 3, b = 4, total = 0;
    void say(std::string_view s) { total += static_cast<int32_t>(s.size()); }
};
inline std::tuple<int32_t, int32_t> outs(const Pairs& q) { return {q.a, q.b}; }
inline void add_n(Pairs& q, Item* p) { q.total += p->n_; }

// The free-function form a `@native(..., function=True)` method renders as.
inline ::tpy::Bytes blob_at(const Blobs& b, int32_t n) {
    return ::tpy::Bytes(b.data.begin(), b.data.begin() + n);
}
}
