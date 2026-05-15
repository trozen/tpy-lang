// Pascal Graph SDL2 display layer.
//
// Optional companion to the pure-TPy Pascal Graph unit. Compiled in when
// `--dsl-opt pascal.sdl=on` (or `auto` finds libsdl2-dev). The frontend
// plugin injects calls to the entry points below around the Pascal source
// (initgraph -> open, delay/setvisualpage -> present, closegraph ->
// show_blocking + closegraph + close_window) so animated TP7 programs
// render live in a window rather than as one final snapshot.
//
// The Pascal Graph runtime owns the pixel buffer + does all drawing
// (Tier A/B/C primitives are pure TPy). This file is a thin display
// adapter -- window/renderer/texture creation, pixel upload, event
// pumping, teardown.

#pragma once

#include <SDL2/SDL.h>

#include <cstdint>
#include <vector>


namespace tpy::pascal {

// Module-level SDL handles. Held in a function-local static so we can
// keep the API as free functions matching the TPy `@native` shape. The
// program is single-threaded at the Pascal level, so a single global
// is fine; nested initgraph/closegraph cycles work because
// `sdl_close_window` resets everything.
struct SdlGfxState {
    SDL_Window* win = nullptr;
    SDL_Renderer* ren = nullptr;
    SDL_Texture* tex = nullptr;
    int width = 0;
    int height = 0;
    bool user_quit = false;
};

inline SdlGfxState& sdl_gfx_state() {
    static SdlGfxState s;
    return s;
}

// Pump any pending SDL events so the OS keeps treating the window as
// responsive. Sets `user_quit` if the user has tried to close the
// window or pressed any key. Non-blocking; safe to call from any
// hook.
inline void sdl_pump_events() {
    SdlGfxState& s = sdl_gfx_state();
    SDL_Event ev;
    while (SDL_PollEvent(&ev) != 0) {
        if (ev.type == SDL_QUIT) {
            s.user_quit = true;
        } else if (ev.type == SDL_KEYDOWN) {
            s.user_quit = true;
        } else if (ev.type == SDL_WINDOWEVENT
                   && ev.window.event == SDL_WINDOWEVENT_CLOSE) {
            s.user_quit = true;
        }
    }
}

// Open the SDL window for a `width`x`height` canvas. Idempotent --
// calling twice with the same dimensions is a no-op; calling with
// different dimensions reopens. Silent no-op on any SDL failure --
// the Pascal program's PPM snapshot fallback still gets written at
// CloseGraph, so users without a working SDL2 setup don't end up
// empty-handed.
inline void sdl_open_window(std::int32_t width, std::int32_t height) {
    SdlGfxState& s = sdl_gfx_state();
    if (width <= 0 || height <= 0) {
        return;
    }
    if (s.win != nullptr && s.width == width && s.height == height) {
        return;
    }
    // Different dimensions on a re-init -> tear down and reopen.
    if (s.win != nullptr) {
        if (s.tex != nullptr) { SDL_DestroyTexture(s.tex); }
        if (s.ren != nullptr) { SDL_DestroyRenderer(s.ren); }
        SDL_DestroyWindow(s.win);
        s.tex = nullptr; s.ren = nullptr; s.win = nullptr;
    }
    if (SDL_WasInit(SDL_INIT_VIDEO) == 0) {
        if (SDL_Init(SDL_INIT_VIDEO) != 0) {
            return;
        }
    }
    // Scale up by 2x so a 320x200 BGI canvas renders at 640x400
    // (closer to readable on a modern display). The texture stays at
    // the canvas size; SDL's RenderCopy stretches it to the window.
    const int win_w = width * 2;
    const int win_h = height * 2;
    s.win = SDL_CreateWindow(
        "TurboPython Pascal Graph",
        SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
        win_w, win_h, SDL_WINDOW_SHOWN);
    if (s.win == nullptr) {
        return;
    }
    s.ren = SDL_CreateRenderer(s.win, -1, 0);
    if (s.ren == nullptr) {
        SDL_DestroyWindow(s.win); s.win = nullptr;
        return;
    }
    s.tex = SDL_CreateTexture(
        s.ren, SDL_PIXELFORMAT_RGB888,
        SDL_TEXTUREACCESS_STREAMING, width, height);
    if (s.tex == nullptr) {
        SDL_DestroyRenderer(s.ren); s.ren = nullptr;
        SDL_DestroyWindow(s.win); s.win = nullptr;
        return;
    }
    s.width = width;
    s.height = height;
    s.user_quit = false;
    sdl_pump_events();
}

// Upload the current pixel buffer and present it. Pumps SDL events so
// the OS keeps the window responsive. Non-blocking. Auto-opens the
// window if it isn't open yet (lets a program call `sdl_present`
// without an explicit `sdl_open_window` -- the frontend injects the
// open call after initgraph but a malformed sequence shouldn't
// silently drop frames).
inline void sdl_present(std::int32_t width, std::int32_t height,
                        const std::vector<std::int32_t>& pixels) {
    SdlGfxState& s = sdl_gfx_state();
    if (width <= 0 || height <= 0) {
        return;
    }
    if (static_cast<std::size_t>(width) * static_cast<std::size_t>(height)
            != pixels.size()) {
        return;
    }
    if (s.win == nullptr || s.width != width || s.height != height) {
        sdl_open_window(width, height);
        if (s.win == nullptr) {
            return;
        }
    }
    SDL_UpdateTexture(s.tex, nullptr, pixels.data(),
                      static_cast<int>(width * sizeof(std::int32_t)));
    SDL_RenderClear(s.ren);
    SDL_RenderCopy(s.ren, s.tex, nullptr, nullptr);
    SDL_RenderPresent(s.ren);
    sdl_pump_events();
}

// Present the final canvas and block until the user closes the
// window or presses any key. If the user already quit during an
// earlier present (`user_quit` set), returns immediately so the
// program can finish teardown without re-blocking.
inline void sdl_show_blocking(std::int32_t width, std::int32_t height,
                              const std::vector<std::int32_t>& pixels) {
    sdl_present(width, height, pixels);
    SdlGfxState& s = sdl_gfx_state();
    if (s.win == nullptr) {
        return;
    }
    if (s.user_quit) {
        return;
    }
    SDL_Event ev;
    while (SDL_WaitEvent(&ev) != 0) {
        if (ev.type == SDL_QUIT
            || ev.type == SDL_KEYDOWN
            || (ev.type == SDL_WINDOWEVENT
                && ev.window.event == SDL_WINDOWEVENT_CLOSE)) {
            s.user_quit = true;
            return;
        }
    }
}

// Destroy the window / renderer / texture and shut down SDL video.
// Idempotent -- safe to call from cleanup paths even when the window
// failed to open.
inline void sdl_close_window() {
    SdlGfxState& s = sdl_gfx_state();
    if (s.tex != nullptr) { SDL_DestroyTexture(s.tex); s.tex = nullptr; }
    if (s.ren != nullptr) { SDL_DestroyRenderer(s.ren); s.ren = nullptr; }
    if (s.win != nullptr) { SDL_DestroyWindow(s.win); s.win = nullptr; }
    s.width = 0;
    s.height = 0;
    s.user_quit = false;
    if (SDL_WasInit(SDL_INIT_VIDEO) != 0) {
        SDL_Quit();
    }
}

}  // namespace tpy::pascal
