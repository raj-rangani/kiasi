import sys
import os
lines = open(sys.argv[1]).read().replace(os.path.expanduser("~"), "~").splitlines()
DIM, BOLD, YEL, END = "\033[2m", "\033[1m", "\033[33m", "\033[0m"
mark = next(i for i, l in enumerate(lines) if l.startswith("[kiasi "))
head, tail = lines[:mark], lines[mark + 1:]
print("\033[1A\033[2K", end="")  # erase the echoed command line
for l in head[:7]: print(l)
print(f"{DIM}      ⋮  {len(head) - 7} more lines of the head{END}")
print(f"{BOLD}{YEL}{lines[mark]}{END}")
print(f"{DIM}      ⋮  {len(tail) - 5} more lines of the tail{END}")
for l in tail[-5:]: print(l)
