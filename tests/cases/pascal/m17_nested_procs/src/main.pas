{ M17: nested procedures / functions. The inner routine is
  declared inside `outer`'s local-decl section and is only
  callable from outer's body. The translator lowers it as a
  module-level function with a mangled name (`outer__inner`); the
  bare-name call inside outer's body is routed via the mangled
  form. Closures (access to outer locals) are not supported -- the
  inner must communicate via parameters. }
program NestedProcs;

procedure outer(start: integer);
  function helper(n: integer): integer;
  begin
    helper := n * n;
  end;
  procedure print_pair(a, b: integer);
  begin
    writeln(a, '->', b);
  end;
begin
  print_pair(start, helper(start));
  print_pair(start + 1, helper(start + 1));
end;

begin
  outer(5);
end.
