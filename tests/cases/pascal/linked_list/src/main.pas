{ M15: pointers + forward type references. Builds a small linked
  list of integers using `^Node` -- the canonical Pascal pointer
  idiom that also exercises forward type declarations (PNode names
  Node before Node is declared in the same type block). Also walks
  the list with a `while` + nil-check loop and disposes nodes on
  exit. }
program LinkedList;
type
  PNode = ^Node;
  Node = record
    value: integer;
    next: PNode;
  end;
var
  head, cur, tmp: PNode;
  i, sum: integer;
begin
  head := nil;
  for i := 5 downto 1 do
  begin
    new(cur);
    cur^.value := i * i;
    cur^.next := head;
    head := cur;
  end;

  cur := head;
  sum := 0;
  while cur <> nil do
  begin
    writeln(cur^.value);
    sum := sum + cur^.value;
    cur := cur^.next;
  end;
  write('sum: ');
  writeln(sum);

  cur := head;
  while cur <> nil do
  begin
    tmp := cur^.next;
    dispose(cur);
    cur := tmp;
  end;
end.
