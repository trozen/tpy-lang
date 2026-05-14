{ M16: variant records. `Shape` is a record whose `case kind: ShapeKind of`
  section gives three variants -- circle, rectangle, triangle. The
  translator generates one inner record per variant and an `Shape.payload`
  union; the discriminant (`kind`) and per-variant fields surface as
  `@property` accessors that match the live payload variant and panic
  on a wrong-variant read/write. }
program VariantRecord;
type
  ShapeKind = (ScCircle, ScRect, ScTri);
  Shape = record
    id: integer;
    case kind: ShapeKind of
      ScCircle: (radius: real);
      ScRect:   (w, h: real);
      ScTri:    (a, b, c: real);
  end;

function area(var s: Shape): real;
begin
  case s.kind of
    ScCircle: area := 3.14159 * s.radius * s.radius;
    ScRect:   area := s.w * s.h;
    ScTri:    area := 0.5 * s.a * s.b;
  end;
end;

var
  s: Shape;
begin
  s.id := 1;
  s.kind := ScCircle;
  s.radius := 2.0;
  write('id=');
  write(s.id);
  write(' area=');
  writeln(area(s));

  s.id := 2;
  s.kind := ScRect;
  s.w := 3.0;
  s.h := 4.0;
  write('id=');
  write(s.id);
  write(' area=');
  writeln(area(s));

  s.id := 3;
  s.kind := ScTri;
  s.a := 5.0;
  s.b := 6.0;
  s.c := 7.0;
  write('id=');
  write(s.id);
  write(' area=');
  writeln(area(s));
end.
