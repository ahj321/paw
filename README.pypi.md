# paw-control

A pair of hands for your agent: screenshot, click, type. One command, one thing.

`paw` is a tiny command-line controller for Windows. It gives vision models and
agents the last three kilometres: looking at the screen and actually pressing the
button. It is not tied to any single agent - the CLI *is* the interface, and an
MCP server comes as a bonus.

## Install

```bash
pip install paw-control
```

Only dependency: Pillow. Everything else is Windows API.

## Quick start

```bash
paw size                     # screen size in physical pixels
paw shot screen.png          # screenshot to a file
paw click 960 540            # left click at 960,540
paw type "hello"             # type text (SendInput, unicode, no clipboard)
paw key enter
paw focus "Notepad"          # bring a window to front by title
paw uia "Notepad"            # list UI Automation controls with exact rects
paw uia-click "Notepad" "Save"   # click a control by name
paw --help                   # 30 subcommands, all documented
```

## Use it with an agent

The command line is the interface: hand the model the output of `paw shot` and
let it decide the next `paw click`. If your client speaks MCP, run:

```bash
python -m paw_mcp
```

and add a `mcpServers` entry pointing at `paw_mcp.py` (see the repository).

## Links

- Source, Chinese README, changelog and pitfalls: https://github.com/ahj321/paw
- Licence: MIT
