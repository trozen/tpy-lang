#pragma once
#include <cstdint>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

namespace ne {

struct Cell {
    int32_t n;
};



// An element-typed container binding its subscript dunders to members of
// its own; `data` is what the declared unchecked twins index directly.
template <typename T>
struct Ring {
    std::vector<T> data;
    auto begin() { return data.begin(); }
    auto end() { return data.end(); }
    auto begin() const { return data.begin(); }
    auto end() const { return data.end(); }
    int32_t size() const { return static_cast<int32_t>(data.size()); }
    // `len()` renders the runtime's free `__len__`, which calls this.
    int32_t __len__() const { return size(); }
    T& at(int32_t i) { return data.at(static_cast<std::size_t>(i)); }
    const T& at(int32_t i) const { return data.at(static_cast<std::size_t>(i)); }
    void put(int32_t i, T v) { data.at(static_cast<std::size_t>(i)) = std::move(v); }
    void push(T v) { data.push_back(std::move(v)); }
};

// The unchecked element read, named by its element type: the type argument
// is what the generic twin's `{T}` placeholder spells.
template <typename T, typename R>
decltype(auto) raw(R& r, int32_t i) {
    return static_cast<std::conditional_t<std::is_const_v<R>, const T&, T&>>(
        r.data[static_cast<std::size_t>(i)]);
}

// A keyed container whose getter, setter and deleter take their key by
// value, so a view key must arrive as an owned string.
struct Keys {
    std::string erased;
    std::string seen;
    void erase(std::string key) { erased += key + ";"; }
    int32_t get(std::string key) {
        seen += key + ";";
        return static_cast<int32_t>(key.size());
    }
    void put(std::string key, int32_t v) {
        seen += key + "=" + std::to_string(v) + ";";
    }
};

// A ring holding one cell of count `n`.
inline Ring<Cell> cell_ring(int32_t n) {
    Ring<Cell> r;
    r.push(Cell{n});
    return r;
}

inline Ring<Cell> cell_plain(int32_t n) { return cell_ring(n); }
inline Ring<Cell> cell_frozen(int32_t n) { return cell_ring(n); }
inline Ring<Cell> cell_renamed(int32_t n) { return cell_ring(n); }
inline Ring<Cell> cell_inherits_len(int32_t n) { return cell_ring(n); }
inline Ring<Cell> cell_inherits_renamed(int32_t n) { return cell_ring(n); }

inline Ring<int32_t> int_plain(int32_t n) {
    Ring<int32_t> r;
    r.push(n);
    return r;
}

}  // namespace ne
