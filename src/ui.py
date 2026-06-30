"""Interactive ncurses UI showing the repository status."""
from .utils import UserMessage, repo_status_cells


def run_ui(repos):
    """interactive ncurses UI showing the repository status

Navigate the scrollable table with j/k (or the arrow keys). Press f to
fetch and P to push the selected repository, and q to quit.
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
        rows.append({'repo': r, 'cells': repo_status_cells(r, ', ')})
    curses.wrapper(_ui_main, rows)


def _ui_main(stdscr, rows):
    import curses
    curses.curs_set(0)
    header = ["repository", "", "uncommited", "push needed", "merge needed"]
    footer = "j/↓: down  k/↑: up  f: fetch  P: push  r: refresh  q: quit"
    sel = 0
    top = 0
    while True:
        h, w = stdscr.getmaxyx()
        width = max(1, w - 1)
        # determine the column widths from the header and every cell
        widths = [len(c) for c in header]
        for row in rows:
            for i, c in enumerate(row['cells']):
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
            line = fmt(rows[ri]['cells']).ljust(width)
            attr = curses.A_REVERSE if ri == sel else curses.A_NORMAL
            stdscr.addnstr(body_top + idx, 0, line, width, attr)
        # key bindings at the bottom of the screen
        stdscr.addnstr(h - 1, 0, footer, width, curses.A_BOLD)
        stdscr.refresh()
        ch = stdscr.getch()
        if ch in (ord('q'), 27):
            break
        elif ch in (ord('j'), curses.KEY_DOWN):
            sel = min(sel + 1, len(rows) - 1)
        elif ch in (ord('k'), curses.KEY_UP):
            sel = max(sel - 1, 0)
        elif ch == ord('f'):
            _ui_action(stdscr, rows, sel, 'fetch')
        elif ch == ord('P'):
            _ui_action(stdscr, rows, sel, 'push')
        elif ch == ord('r'):
            # recompute the status of every repository
            for row in rows:
                row['cells'] = repo_status_cells(row['repo'], ', ')


def _ui_action(stdscr, rows, sel, action):
    import curses
    r = rows[sel]['repo']
    # leave curses mode so the git output appears on the normal terminal
    curses.endwin()
    try:
        if action == 'fetch':
            print("Fetching {} ...".format(r.tilde_path))
            r.fetch()
        elif action == 'push':
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
