{ Manual demo for the optional SDL2 display layer (M22). NOT a
  pytest case -- the regular test suite must stay free of system
  SDL2 dependency. Run locally with libsdl2-dev installed:

      sudo apt install libsdl2-dev   (or your distro's equivalent)
      cd examples/frontends/pascal/sdl_demo
      uv run --project ../../../.. tpy main.pas

  A window with a drawn scene opens; press any key or close the
  window to exit. The same drawing also writes `out.ppm` in the
  build dir, so users without SDL2 can run the program too --
  they just won't get the interactive window. }
program SDLDemo;
uses Graph, GraphSDL;
const
  Red = 4;
  Green = 2;
  Yellow = 14;
  White = 15;
  Blue = 1;
  Cyan = 3;
  LightBlue = 9;
var
  gd, gm: integer;
begin
  gd := 0;
  gm := 0;  { default 320x200 }
  initgraph(gd, gm, '');

  setcolor(LightBlue);
  bar(0, 0, getmaxx(), getmaxy());

  setcolor(Yellow);
  fillellipse(60, 60, 25, 25);    { sun }

  setcolor(Green);
  bar(0, 150, getmaxx(), getmaxy());  { ground }

  setcolor(Red);
  rectangle(100, 100, 200, 150);   { house body }
  setcolor(Yellow);
  bar(101, 101, 199, 149);
  setcolor(Red);
  line(100, 100, 150, 70);
  line(150, 70, 200, 100);          { roof }

  setcolor(White);
  outtextxy(80, 20, 'HELLO BGI');

  show;
  closegraph;
end.
