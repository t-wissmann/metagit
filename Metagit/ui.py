"""Interactive ncurses UI showing the repository status.

Repositories can run a command (e.g. fetch) in the background. While such a
command is running, its status is shown in place of the commit counts next to
the repository name; when it finishes the row is refreshed with the new status.

The key bindings are configurable: the 'keys' section of the config maps a key
to an action string. The available actions are the names registered in
_ACTIONS below; 'run-bg' and 'run-fg' take the git command to run as an
argument (e.g. 'run-bg git fetch').
"""
import threading

from .utils import UserMessage, repo_status_cells


# frames of the rotating bar shown while a background command runs
_SPINNER = "|/—\\"


def run_ui(repos, keys):
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
    curses.wrapper(_ui_main, rows, keys)


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
    """the cells to render for a row, accounting for a background command.

    A running (or failed) command replaces the two commit-count columns with
    its status text.
    """
    cells = list(row['cells'])
    bg = row['bg']
    if bg is None:
        return cells
    while len(cells) < 5:
        cells.append('')
    if not bg['finished']:
        status = _SPINNER[tick % len(_SPINNER)] + ' ' + bg['command']
    else:
        status = bg['command'] + ' failed'
    cells[3] = status
    cells[4] = ''
    return cells


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


# --- actions ---------------------------------------------------------------
#
# Each action is a function (state, arg) -> None registered in _ACTIONS. `arg`
# is the remainder of the action string (used by run-bg/run-fg to carry the
# command line).

class _UIState:
    def __init__(self, stdscr, rows):
        self.stdscr = stdscr
        self.rows = rows
        self.sel = 0
        self.top = 0
        self.tick = 0
        self.running = True


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
    row = state.rows[state.sel]
    repo = row['repo']
    # leave curses mode so the git output appears on the normal terminal
    curses.endwin()
    try:
        print("Running {} in {} ...".format(arg, repo.tilde_path))
        _run_repo_command(repo, arg, live=True)
    except UserMessage as e:
        print("Error: {}".format(e))
    try:
        input("Press enter to continue...")
    except (EOFError, KeyboardInterrupt):
        pass
    # refresh the status of the affected repository
    row['cells'] = repo_status_cells(repo, ', ')
    state.stdscr.clear()
    state.stdscr.refresh()


_ACTIONS = {
    'down': _action_down,
    'up': _action_up,
    'quit': _action_quit,
    'refresh': _action_refresh,
    'run-bg': _action_run_bg,
    'run-fg': _action_run_fg,
}


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

    Recognises the arrow-key symbols and otherwise takes a single character
    verbatim. Returns None for anything it can not map.
    """
    special = {
        '↑': curses.KEY_UP,
        '↓': curses.KEY_DOWN,
        '←': curses.KEY_LEFT,
        '→': curses.KEY_RIGHT,
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

def _ui_main(stdscr, rows, keys):
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
    keymap = _build_keymap(keys, curses)
    header = ["repository", "", "uncommited", "push needed", "merge needed"]
    footer = '  '.join('{}: {}'.format(k, spec) for k, spec in keys.items())
    state = _UIState(stdscr, rows)
    while state.running:
        _reap_background(rows)
        h, w = stdscr.getmaxyx()
        width = max(1, w - 1)
        display = [_display_cells(row, state.tick) for row in rows]
        # determine the column widths from the header and every cell
        widths = [len(c) for c in header]
        for cells in display:
            for i, c in enumerate(cells):
                if i < len(widths):
                    widths[i] = max(widths[i], len(c))
        def fmt(cells):
            parts = []
            for i in range(len(widths)):
                c = cells[i] if i < len(cells) else ""
                parts.append(('{:' + str(widths[i]) + 's}').format(c))
            return '  '.join(parts)
        body_top = 2
        body_height = max(1, h - body_top - 1)
        # keep the selected row within the visible window
        if state.sel < state.top:
            state.top = state.sel
        elif state.sel >= state.top + body_height:
            state.top = state.sel - body_height + 1
        stdscr.erase()
        # fixed table head
        stdscr.addnstr(0, 0, fmt(header), width, curses.A_BOLD)
        stdscr.addnstr(1, 0, '─' * width, width)
        # scrollable body
        for idx in range(body_height):
            ri = state.top + idx
            if ri >= len(rows):
                break
            line = fmt(display[ri]).ljust(width)
            attr = curses.A_REVERSE if ri == state.sel else curses.A_NORMAL
            stdscr.addnstr(body_top + idx, 0, line, width, attr)
        # key bindings at the bottom of the screen
        stdscr.addnstr(h - 1, 0, footer, width, curses.A_BOLD)
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
