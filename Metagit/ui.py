"""Interactive ncurses UI showing the repository status.

Repositories can run a command (e.g. fetch) in the background. While such a
command is running, its status is shown in place of the commit counts next to
the repository name; when it finishes the row is refreshed with the new status.
"""
import threading

from .utils import UserMessage, repo_status_cells


# frames of the rotating bar shown while a background command runs
_SPINNER = "|/—\\"


def run_ui(repos):
    """interactive ncurses UI showing the repository status

Navigate the scrollable table with j/k (or the arrow keys). Press f to
fetch (in the background) and P to push the selected repository, and q to
quit.
"""
    import locale
    locale.setlocale(locale.LC_ALL, '')
    try:
        import curses
    except ImportError:
        raise UserMessage("curses is not available on this platform")
    if not repos:
        raise UserMessage("No repositories are configured")
    rows = []
    for p, r in repos.items():
        rows.append({'repo': r, 'cells': repo_status_cells(r, ', '), 'bg': None})
    curses.wrapper(_ui_main, rows)


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


def _ui_main(stdscr, rows):
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
    header = ["repository", "", "uncommited", "push needed", "merge needed"]
    footer = "j/↓: down  k/↑: up  f: fetch  P: push  r: refresh  q: quit"
    sel = 0
    top = 0
    tick = 0
    while True:
        _reap_background(rows)
        h, w = stdscr.getmaxyx()
        width = max(1, w - 1)
        display = [_display_cells(row, tick) for row in rows]
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
        if sel < top:
            top = sel
        elif sel >= top + body_height:
            top = sel - body_height + 1
        stdscr.erase()
        # fixed table head
        stdscr.addnstr(0, 0, fmt(header), width, curses.A_BOLD)
        stdscr.addnstr(1, 0, '─' * width, width)
        # scrollable body
        for idx in range(body_height):
            ri = top + idx
            if ri >= len(rows):
                break
            line = fmt(display[ri]).ljust(width)
            attr = curses.A_REVERSE if ri == sel else curses.A_NORMAL
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
            tick += 1
            continue
        if ch in (ord('q'), 27):
            break
        elif ch in (ord('j'), curses.KEY_DOWN):
            sel = min(sel + 1, len(rows) - 1)
        elif ch in (ord('k'), curses.KEY_UP):
            sel = max(sel - 1, 0)
        elif ch == ord('f'):
            r = rows[sel]['repo']
            _start_background(rows[sel], 'git fetch',
                              lambda repo=r: repo.fetch(quiet=True))
        elif ch == ord('P'):
            _ui_action(stdscr, rows, sel, 'push')
        elif ch == ord('r'):
            # recompute the status of every repository, dropping any finished
            # background command
            for row in rows:
                if row['bg'] is not None and not row['bg']['finished']:
                    continue
                row['bg'] = None
                row['cells'] = repo_status_cells(row['repo'], ', ')


def _ui_action(stdscr, rows, sel, action):
    import curses
    r = rows[sel]['repo']
    # leave curses mode so the git output appears on the normal terminal
    curses.endwin()
    try:
        if action == 'push':
            print("Pushing {} ...".format(r.tilde_path))
            r.push()
    except UserMessage as e:
        print("Error: {}".format(e))
    try:
        input("Press enter to continue...")
    except (EOFError, KeyboardInterrupt):
        pass
    # refresh the status of the affected repository
    rows[sel]['cells'] = repo_status_cells(r, ', ')
    stdscr.clear()
    stdscr.refresh()
