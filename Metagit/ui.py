"""Interactive ncurses UI showing the repository status.

Repositories can run a command (e.g. fetch) in the background. While such a
command is running, its status is shown in place of the commit counts next to
the repository name; when it finishes the row is refreshed with the new status.

The key bindings are configurable: the 'keys' section of the config maps a key
to an action string. The available actions are the names registered in
_ACTIONS below; 'run-bg' and 'run-fg' take the git command to run as an
argument (e.g. 'run-bg git fetch').

The colors are configurable too: the 'colors' section of the config maps a UI
element (see _CELL_COLORS and the header/selected entries) to a color/attribute
spec parsed by _ColorScheme.
"""
import threading

from .utils import UserMessage, repo_status_cells


# frames of the rotating bar shown while a background command runs
_SPINNER = "|/—\\"

# the color name used for each status column, indexed like the cells returned
# by repo_status_cells (None leaves the column in the default color)
_CELL_COLORS = [None, 'not-present', 'uncommited', 'push-needed', 'merge-needed']


def run_ui(repos, keys, colors=None, run_fg_prompt_threshold=5,
           documentation=None):
    """interactive ncurses UI showing the repository status

Navigate the scrollable table and act on the selected repository with the
configured key bindings (see the 'keys' section of the config).
"""
    import locale
    locale.setlocale(locale.LC_ALL, '')
    try:
        import curses
    except ImportError:
        raise UserMessage("curses is not available on this platform")
    rows = []
    for p, r in repos.items():
        rows.append({'repo': r, 'cells': repo_status_cells(r, ', '), 'bg': None})
    curses.wrapper(_ui_main, rows, keys, colors or {},
                   run_fg_prompt_threshold, documentation)


class _ColorScheme:
    """translate the configured color specs into curses attributes.

    A spec is a space separated list of tokens: a foreground color name, 'on
    <color>' for the background color, and any number of attribute names. Color
    pairs are allocated lazily as (foreground, background) combinations are
    requested. Anything that can not be mapped (e.g. no color support in the
    terminal) degrades gracefully to just the attributes it understood.
    """

    def __init__(self, specs, curses):
        self._curses = curses
        self._colors = {
            'default': -1,
            'black': curses.COLOR_BLACK,
            'red': curses.COLOR_RED,
            'green': curses.COLOR_GREEN,
            'yellow': curses.COLOR_YELLOW,
            'blue': curses.COLOR_BLUE,
            'magenta': curses.COLOR_MAGENTA,
            'cyan': curses.COLOR_CYAN,
            'white': curses.COLOR_WHITE,
        }
        self._attrs = {
            'normal': curses.A_NORMAL,
            'bold': curses.A_BOLD,
            'dim': curses.A_DIM,
            'reverse': curses.A_REVERSE,
            'underline': curses.A_UNDERLINE,
            'standout': curses.A_STANDOUT,
            'blink': curses.A_BLINK,
        }
        # (fg, bg) -> allocated pair index; pair 0 and 1 are reserved (1 is the
        # transparent background set up in _ui_main), so start allocating at 2
        self._pairs = {}
        self._next_pair = 2
        self._resolved = {name: self._parse(spec)
                          for name, spec in (specs or {}).items()}

    def get(self, name, default=0):
        """the curses attribute for a configured element, or `default`"""
        return self._resolved.get(name, default)

    def _pair(self, fg, bg):
        key = (fg, bg)
        if key not in self._pairs:
            idx = self._next_pair
            try:
                self._curses.init_pair(idx, fg, bg)
            except self._curses.error:
                # no (more) color pairs available: fall back to no color
                self._pairs[key] = 0
                return 0
            self._pairs[key] = idx
            self._next_pair += 1
        return self._pairs[key]

    def _parse(self, spec):
        fg, bg, attr = -1, -1, 0
        tokens = str(spec).split()
        idx = 0
        while idx < len(tokens):
            token = tokens[idx].lower()
            if token == 'on' and idx + 1 < len(tokens):
                bg = self._colors.get(tokens[idx + 1].lower(), -1)
                idx += 2
                continue
            if token in self._attrs:
                attr |= self._attrs[token]
            elif token in self._colors:
                fg = self._colors[token]
            idx += 1
        if fg != -1 or bg != -1:
            attr |= self._curses.color_pair(self._pair(fg, bg))
        return attr


# --- background command handling ------------------------------------------

def _start_background(row, command, func):
    """run func() in a background thread, tracking its state on the row.

    `command` is the literal command line shown while it runs (with a spinner)
    and, on failure, followed by 'failed'. Only one background command runs per
    repository at a time; a second request is ignored while one is still in
    flight.
    """
    bg = row['bg']
    if bg is not None and not bg['finished']:
        return
    bg = {
        'command': command,
        'finished': False,
        'error': None,
        'handled': False,
    }

    def worker():
        try:
            func()
        except Exception as e:
            bg['error'] = str(e)
        finally:
            bg['finished'] = True

    bg['thread'] = threading.Thread(target=worker, daemon=True)
    row['bg'] = bg
    bg['thread'].start()


def _reap_background(rows):
    """fold successfully finished background commands back into the status.

    A command that failed is left in place so its failure stays visible until
    the row is refreshed.
    """
    for row in rows:
        bg = row['bg']
        if bg is None or not bg['finished'] or bg['handled']:
            continue
        bg['handled'] = True
        bg['thread'].join()
        if bg['error'] is None:
            row['cells'] = repo_status_cells(row['repo'], ', ')
            row['bg'] = None


def _display_cells(row, tick):
    """the (text, color-name) cells to render for a row.

    The color name is the key looked up in the color scheme (None for the
    default color). A running (or failed) background command replaces the two
    commit-count columns with its status text and its own color.
    """
    cells = list(row['cells'])
    while len(cells) < 5:
        cells.append('')
    colors = list(_CELL_COLORS)
    bg = row['bg']
    if bg is not None:
        if not bg['finished']:
            cells[3] = _SPINNER[tick % len(_SPINNER)] + ' ' + bg['command']
            colors[3] = 'running'
        else:
            cells[3] = bg['command'] + ' failed'
            colors[3] = 'failed'
        cells[4] = ''
        colors[4] = None
    return list(zip(cells, colors))


def _run_repo_command(repo, command, live=False):
    """run a user command in a repository, skipping repos that do not exist.

    `command` is a full command line (e.g. 'git fetch') run through the shell
    with the repository as the working directory.
    """
    if not repo.exists():
        return
    if live:
        # let the command write straight to the terminal (foreground command)
        repo.call(command, shell=True, stderr=None)
    else:
        repo.call(command, shell=True, quiet=True)


def page_text(text):
    """pipe `text` through the user's $PAGER, falling back to stdout.

    Used both by the CLI 'help' command and the interactive 'help' action.
    """
    import os
    import sys
    import subprocess
    pager = os.environ.get('PAGER', 'less')
    try:
        proc = subprocess.Popen(pager, shell=True,
                                stdin=subprocess.PIPE, text=True)
    except OSError:
        # no usable pager: fall back to writing straight to stdout
        sys.stdout.write(text)
        return
    try:
        proc.communicate(text)
    except (BrokenPipeError, KeyboardInterrupt):
        # the user quit the pager early
        pass


# --- actions ---------------------------------------------------------------
#
# Each action is a function (state, arg) -> None registered in _ACTIONS. `arg`
# is the remainder of the action string (used by run-bg/run-fg to carry the
# command line).

class _UIState:
    def __init__(self, stdscr, rows, run_fg_prompt_threshold=5,
                 documentation=None):
        self.stdscr = stdscr
        self.rows = rows
        self.sel = 0
        self.top = 0
        self.tick = 0
        self.running = True
        self.run_fg_prompt_threshold = run_fg_prompt_threshold
        # callable returning the documentation string shown by the 'help'
        # action (None disables it)
        self.documentation = documentation


def _action_down(state, arg):
    if state.rows:
        state.sel = min(state.sel + 1, len(state.rows) - 1)


def _action_up(state, arg):
    if state.rows:
        state.sel = max(state.sel - 1, 0)


def _action_quit(state, arg):
    state.running = False


def _action_refresh(state, arg):
    # recompute the status of every repository, dropping any finished
    # background command
    for row in state.rows:
        if row['bg'] is not None and not row['bg']['finished']:
            continue
        row['bg'] = None
        row['cells'] = repo_status_cells(row['repo'], ', ')


def _action_run_bg(state, arg):
    if not state.rows:
        return
    row = state.rows[state.sel]
    _start_background(row, arg,
                      lambda repo=row['repo']: _run_repo_command(repo, arg))


def _action_run_fg(state, arg):
    if not state.rows:
        return
    import curses
    import time
    row = state.rows[state.sel]
    repo = row['repo']
    # leave curses mode so the git output appears on the normal terminal
    curses.endwin()
    start = time.monotonic()
    try:
        print("Running {} in {} ...".format(arg, repo.tilde_path))
        _run_repo_command(repo, arg, live=True)
    except UserMessage as e:
        print("Error: {}".format(e))
    # only prompt when the command was quick; long-running commands
    # (e.g. an interactive shell) don't need a manual confirmation
    if time.monotonic() - start < state.run_fg_prompt_threshold:
        try:
            input("Press enter to continue...")
        except (EOFError, KeyboardInterrupt):
            pass
    # refresh the status of the affected repository
    row['cells'] = repo_status_cells(repo, ', ')
    state.stdscr.clear()
    state.stdscr.refresh()


def _action_help(state, arg):
    if state.documentation is None:
        return
    import curses
    # leave curses mode so the pager gets the normal terminal
    curses.endwin()
    page_text(state.documentation())
    state.stdscr.clear()
    state.stdscr.refresh()


_ACTIONS = {
    'down': _action_down,
    'up': _action_up,
    'quit': _action_quit,
    'refresh': _action_refresh,
    'run-bg': _action_run_bg,
    'run-fg': _action_run_fg,
    'help': _action_help,
}


# human readable description for every action, keyed by its name. 'run-bg' and
# 'run-fg' take the command line to run as their argument.
_ACTION_DOCS = {
    'down': 'move the selection down one repository',
    'up': 'move the selection up one repository',
    'quit': 'quit the interactive UI',
    'refresh': 'recompute the status of every repository',
    'run-bg': 'run the given command in the background for the selected '
              'repository (e.g. "run-bg git fetch")',
    'run-fg': 'run the given command in the foreground for the selected '
              'repository, leaving the UI while it runs (e.g. "run-fg $SHELL")',
    'help': 'show this documentation, paged through $PAGER',
}


def action_docs():
    """the mapping of action name to a human readable description"""
    return dict(_ACTION_DOCS)


def _parse_action(spec):
    """split an action string into its name and argument.

    'run-bg git fetch' -> ('run-bg', 'git fetch'); 'down' -> ('down', '').
    """
    parts = str(spec).split(None, 1)
    if not parts:
        return None, ''
    return parts[0], parts[1] if len(parts) > 1 else ''


def _key_code(keystr, curses):
    """translate a config key string into the code returned by getch().

    Recognises the arrow-key symbols and the '\\n' escape for Return, and
    otherwise takes a single character verbatim. Returns None for anything it
    can not map.
    """
    special = {
        '↑': curses.KEY_UP,
        '↓': curses.KEY_DOWN,
        '←': curses.KEY_LEFT,
        '→': curses.KEY_RIGHT,
        'Enter': ord('\n'),
    }
    if keystr in special:
        return special[keystr]
    if len(keystr) == 1:
        return ord(keystr)
    return None


def _build_keymap(keys, curses):
    """map a getch() code to a (action, arg) pair for the configured keys."""
    keymap = {}
    for keystr, spec in keys.items():
        code = _key_code(str(keystr), curses)
        if code is None:
            continue
        name, arg = _parse_action(spec)
        if name in _ACTIONS:
            keymap[code] = (_ACTIONS[name], arg)
    return keymap


# --- main loop -------------------------------------------------------------

def _draw_row(stdscr, y, cells, widths, base_attr, color, width):
    """draw one table row of (text, color-name) cells at line `y`.

    Each cell is padded to its column width and drawn with `base_attr` combined
    with the cell's own color; `base_attr` (e.g. the selection highlight) also
    fills the gap between columns and the rest of the line.
    """
    x = 0
    for i in range(len(widths)):
        if x >= width:
            return
        text, name = cells[i] if i < len(cells) else ('', None)
        # an empty cell keeps only the row's base attribute (e.g. the selection
        # highlight); its own color would otherwise paint the padded blanks
        cell_attr = color.get(name) if name is not None and text else 0
        text = ('{:' + str(widths[i]) + 's}').format(text)
        attr = base_attr | cell_attr
        stdscr.addnstr(y, x, text, width - x, attr)
        x += widths[i]
        if i < len(widths) - 1 and x < width:
            stdscr.addnstr(y, x, '  ', width - x, base_attr)
            x += 2
    if x < width:
        # extend base_attr (e.g. the selection highlight) to the line's end
        stdscr.addnstr(y, x, ' ' * (width - x), width - x, base_attr)


def _ui_main(stdscr, rows, keys, colors=None, run_fg_prompt_threshold=5,
             documentation=None):
    import curses
    curses.curs_set(0)
    # use the terminal's default background (transparent) instead of black
    try:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, -1, -1)
        stdscr.bkgd(' ', curses.color_pair(1))
    except curses.error:
        pass
    color = _ColorScheme(colors or {}, curses)
    keymap = _build_keymap(keys, curses)
    header = ["repository", "", "uncommited", "push needed", "merge needed"]
    state = _UIState(stdscr, rows, run_fg_prompt_threshold, documentation)
    while state.running:
        _reap_background(rows)
        h, w = stdscr.getmaxyx()
        width = max(1, w - 1)
        display = [_display_cells(row, state.tick) for row in rows]
        # determine the column widths from the header and every cell text
        widths = [len(c) for c in header]
        for cells in display:
            for i, (text, _name) in enumerate(cells):
                if i < len(widths):
                    widths[i] = max(widths[i], len(text))
        body_top = 2
        body_height = max(1, h - body_top)
        # keep the selected row within the visible window
        if state.sel < state.top:
            state.top = state.sel
        elif state.sel >= state.top + body_height:
            state.top = state.sel - body_height + 1
        stdscr.erase()
        # fixed table head (no per-column colors, so a plain (text, None) row)
        header_cells = [(h, None) for h in header]
        _draw_row(stdscr, 0, header_cells, widths,
                  color.get('header', curses.A_BOLD), color, width)
        stdscr.addnstr(1, 0, '─' * width, width)
        # scrollable body
        for idx in range(body_height):
            ri = state.top + idx
            if ri >= len(rows):
                break
            base = color.get('selected', curses.A_REVERSE) \
                if ri == state.sel else curses.A_NORMAL
            _draw_row(stdscr, body_top + idx, display[ri], widths,
                      base, color, width)
        stdscr.refresh()
        # while background commands run, poll so the display keeps updating;
        # otherwise block until the next key press
        busy = any(row['bg'] is not None and not row['bg']['finished']
                   for row in rows)
        stdscr.timeout(200 if busy else -1)
        ch = stdscr.getch()
        if ch == -1:
            state.tick += 1
            continue
        binding = keymap.get(ch)
        if binding is None:
            continue
        action, arg = binding
        action(state, arg)
