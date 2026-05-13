{ M9: realistic end-to-end -- ASCII Mandelbrot. Exercises real
  arithmetic, mixed int/real expressions, nested while loops,
  boolean short-circuit (the `and` guards the bailout), and per-
  character `write` plus row-terminating `writeln`. }
program Mandelbrot;
var
  cx, cy, x, y, tmp: real;
  px, py, iter: integer;
begin
  py := 0;
  while py < 16 do
  begin
    px := 0;
    while px < 60 do
    begin
      cx := -2.0 + 3.0 * px / 60;
      cy := -1.0 + 2.0 * py / 16;
      x := 0.0;
      y := 0.0;
      iter := 0;
      while (iter < 30) and (x*x + y*y < 4.0) do
      begin
        tmp := x*x - y*y + cx;
        y := 2.0 * x * y + cy;
        x := tmp;
        iter := iter + 1;
      end;
      if iter = 30 then write('#') else write(' ');
      px := px + 1;
    end;
    writeln('');
    py := py + 1;
  end;
end.
