"""Terminal model selection; model IDs always come from Mistral's catalog."""
import curses
import json
import os
from pathlib import Path
import sys
import tempfile


def selection_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'claude-mistral' / 'selection.json'


def _selection():
    try:
        value = json.loads(selection_path().read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def remembered_model():
    value = _selection().get('model')
    return value if isinstance(value, str) else None


def remember(model, thinking=None):
    saved = _selection()
    saved['model'] = model
    if thinking is not None:
        levels = saved.get('thinking')
        if not isinstance(levels, dict):
            levels = {}
        levels[model] = thinking
        saved['thinking'] = levels
    path = selection_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
            name = handle.name
            json.dump(saved, handle)
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def choices(catalog):
    seen, result = set(), []
    for item in catalog:
        identifier = item.get('id')
        capabilities = item.get('capabilities') or {}
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            continue
        if capabilities.get('completion_chat') is False or capabilities.get('function_calling') is False:
            continue
        seen.add(identifier)
        label = item.get('label') or item.get('name') or identifier
        result.append((identifier, label if isinstance(label, str) else identifier))
    return result


def pick(catalog):
    items = choices(catalog)
    if not items:
        sys.exit('Mistral returned no models suitable for chat and tool calls.')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        sys.exit('The model picker requires a terminal. Use claude-mistral run --model MODEL_ID.')
    previous = remembered_model()
    index = next((i for i, item in enumerate(items) if item[0] == previous), 0)
    selected = curses.wrapper(_screen, items, index, previous)
    if selected is None:
        sys.exit(0)
    try:
        remember(selected)
    except OSError:
        print('Could not remember the model; continuing with your selection.', file=sys.stderr)
    return selected


def _screen(screen, items, index, previous, heading='Select Model', action='continue'):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    screen.keypad(True)
    top = 0
    while True:
        screen.erase()
        height, width = screen.getmaxyx()
        if height < 5 or width < 24:
            try:
                screen.addnstr(0, 0, 'Enlarge terminal (q cancels)', max(0, width - 1))
            except curses.error:
                pass
        else:
            screen.addnstr(0, 0, heading, width - 1, curses.A_BOLD)
            visible = height - 4
            top = max(0, min(top, index))
            if index >= top + visible:
                top = index - visible + 1
            for row, (identifier, label) in enumerate(items[top:top + visible], 1):
                pos = top + row - 1
                suffix = ' (last used)' if identifier == previous else ''
                # Include the exact ID when the catalog provides a display name.
                title = label if label == identifier else f'{label}  [{identifier}]'
                line = ('› ' if pos == index else '  ') + title + suffix
                screen.addnstr(row, 0, line, width - 1, curses.A_REVERSE if pos == index else 0)
            screen.addnstr(height - 2, 0, f'↑/↓ select · Enter {action} · Esc cancel', width - 1, curses.A_DIM)
        screen.refresh()
        key = screen.getch()
        if key in (27, ord('q')):
            return None
        if key == 3:
            raise KeyboardInterrupt
        if key in (curses.KEY_UP, ord('k')):
            index = (index - 1) % len(items)
        elif key in (curses.KEY_DOWN, ord('j')):
            index = (index + 1) % len(items)
        elif key in (10, 13, curses.KEY_ENTER) and height >= 5 and width >= 24:
            return items[index][0]


def pick_thinking(model, entry=None):
    from .thinking import levels_for
    levels = levels_for(model, entry)
    if not levels:
        return None
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        sys.exit('The thinking picker requires a terminal. Pass --thinking LEVEL.')
    saved = _selection().get('thinking', {})
    previous = saved.get(model) if isinstance(saved, dict) else None
    index = levels.index(previous) if previous in levels else 0
    selected = curses.wrapper(_screen, [(level, level) for level in levels], index,
                              previous, f'Select Thinking Level · {model}', 'launch')
    if selected is None:
        sys.exit(0)
    try:
        remember(model, selected)
    except OSError:
        print('Could not remember thinking level; continuing.', file=sys.stderr)
    return selected
