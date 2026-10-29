#pragma once
#include <cstdint>
#include <vector>
struct V { int32_t a = 0; };
inline V make() { return V{5}; }
inline V other() { return V{6}; }
struct H { V t; int32_t n = 0; };
struct M { H* ph = nullptr; };
inline H g_h;
inline H* find(int32_t) { return &g_h; }
struct Inner { V t; };
struct Outer { Inner inner; };
inline Outer make_outer() { return Outer{}; }
struct Item { int32_t id = 0; };
template <class T> struct Bag {
    std::vector<T> items;
    auto begin() { return items.begin(); }
    auto end() { return items.end(); }
    auto begin() const { return items.begin(); }
    auto end() const { return items.end(); }
};
struct ItemBag : Bag<Item*> {};
inline Item g_item{7};
struct Holder { ItemBag bag; };
inline Holder g_holder;
inline Holder* hold(int32_t) { g_holder.bag.items = {&g_item}; return &g_holder; }
