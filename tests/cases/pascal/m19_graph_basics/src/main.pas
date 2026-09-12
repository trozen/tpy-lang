{ M19: Graph unit Tier-A primitives. Pure-TPy drawing into a
  packed-int32 pixel buffer; tests read pixels back via `GetPixel`
  so the snapshot is text-only. `CloseGraph` writes `out.ppm` to
  the program's cwd (the gitignored build dir under `__tpyc__/`);
  that file is intentionally NOT part of the snapshot. }
program GraphBasics;
uses Graph;
const
  Red = 4;
  Green = 2;
  Yellow = 14;
  White = 15;
var
  gd, gm: integer;
begin
  gd := 0;
  { Smuggle a tiny canvas via mode = width*1000 + height -- our
    initgraph treats nonzero mode as a (w, h) pair. Keeps the
    pixel buffer small enough for tests to read back specific
    coordinates without ambiguity. }
  gm := 40020;  { 40 x 20 canvas }
  initgraph(gd, gm, '');
  writeln('canvas=', getmaxx() + 1, 'x', getmaxy() + 1);

  setcolor(Red);
  putpixel(5, 5, Red);
  writeln('px@5,5=', getpixel(5, 5));

  setcolor(Green);
  line(0, 0, 10, 10);
  writeln('line@5,5=', getpixel(5, 5));
  writeln('line@10,10=', getpixel(10, 10));
  writeln('line@11,11=', getpixel(11, 11));

  setcolor(Yellow);
  rectangle(15, 0, 20, 5);
  writeln('rect_corner@15,0=', getpixel(15, 0));
  writeln('rect_corner@20,5=', getpixel(20, 5));
  writeln('rect_inside@17,2=', getpixel(17, 2));

  setcolor(White);
  bar(25, 10, 30, 15);
  writeln('bar_corner@25,10=', getpixel(25, 10));
  writeln('bar_inside@27,12=', getpixel(27, 12));
  writeln('bar_outside@31,16=', getpixel(31, 16));

  setcolor(Red);
  circle(20, 10, 3);
  writeln('circle_right@23,10=', getpixel(23, 10));
  writeln('circle_top@20,7=', getpixel(20, 7));
  writeln('circle_center@20,10=', getpixel(20, 10));

  closegraph;
  writeln('done');
end.
