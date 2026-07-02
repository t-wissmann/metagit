"""Generic helpers shared across metagit: messaging, prompting and the
table/status formatting used by both the CLI and the ncurses UI.
"""
import os
import re
import sys
import subprocess


class UserMessage(Exception):
    def __init__(self, msg, repo=None):
        self.msg = msg
        self.repo = repo
    def __str__(self):
        return self.msg


debug_messages = False

def set_verbose(enabled):
    global debug_messages
    debug_messages = enabled

def debug(*args):
    if debug_messages:
        print(' '.join(list(args)), file=sys.stderr)

def warning(*args):
    print(' '.join(list(args)), file=sys.stderr)


def ask(question, default = None):
    prompt = ' [{}/{}]'.format(
        ('Y' if default == True else 'y'),
        ('N' if default == False else 'n'))
    answer = input(question + prompt + ' ')
    if len(answer) < 1:
        return default
    else:
        return answer[0].lower() == 'y'


def countshow(cnt, suffix = None, suffix1 = None):
    if cnt == 0:
        return ""
    elif cnt == 1:
        return str(cnt) + ('' if suffix1 is None else ' ' + suffix1)
    else:
        return str(cnt) + ('' if suffix is None else ' ' + suffix)


def pretty_print_table(rows):
    """pretty print a table, given as a list of lists
    The first row is interpreted as the header
    """
    # determine column widths and row heights
    widths = []
    heights = []
    for r in rows:
        row_height = 1
        for idx,c in enumerate(r):
            while len(widths) <= idx:
                widths += [ 0 ]
            cell_lines = str(c).split('\n')
            widths[idx] = max(widths[idx], max([ len(l) for l in cell_lines]) )
            row_height = max(row_height, len(cell_lines))
        heights += [ row_height ]
    outbuf = ""
    toprule = "━" * widths[0]
    midrule = "─" * widths[0]
    for w in widths[1:]:
        toprule += "━"
        toprule += "━" * w
        midrule += "─"
        midrule += "─" * w
    outbuf += toprule + "\n"
    for r_idx,r in enumerate(rows):
        for cell_line in range(heights[r_idx]):
            is_first_column = True
            for idx,c in enumerate(r):
                if widths[idx] <= 0:
                    continue
                if is_first_column:
                    is_first_column = False
                else:
                    outbuf += ' '
                formatstring = '{:' + str(widths[idx]) + 's}'
                cell_content = str(c).split('\n')
                cur_line = ""
                if cell_line < len(cell_content):
                    cur_line = cell_content[cell_line]
                outbuf += formatstring.format(cur_line)
            outbuf += '\n'
        if r_idx == 0:
            outbuf += midrule + "\n"
    outbuf += toprule + "\n"
    print(outbuf)


# return the absolute path of the git root for the current working directory
# without trailing slashes, or None, if cwd does not live in a git repository
def detect_git(cwd='.'):
    cmd = ['git', 'rev-parse', '--show-toplevel']
    proc = subprocess.Popen(cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            cwd=cwd)
    stdout, stderr = proc.communicate()
    status = proc.wait()
    if status == 0 and stderr.decode() == '':
        return re.sub('[/\r\n]*$', '', stdout.decode())
    else:
        return None;


# return an absolute path with the home directory in the path replaced by ~
def tilde_encode(cwd = '.'):
    abspath = os.path.abspath(cwd)
    home = os.path.expanduser('~')
    if home == os.path.commonpath([home, abspath]):
        r = os.path.relpath(abspath, start=home)
        return os.path.join('~', os.path.relpath(abspath, start=home))
    else:
        return abspath


def status_summary(rs, separator='\n'):
    """summarize a RepoStatus as a single (text, color-name) status.

Every kind of pending work is folded into one human readable phrase (e.g.
'2 uncommitted changes', '2 commits need push', '5 commits behind upstream'),
its parts joined by `separator`. The color name is that of the most
significant pending state (a missing checkout first, then local changes, then
commits to push, then commits to merge), or None when the repository is clean.
"""
    if not rs.exists:
        return "not present", 'not-present'
    parts = filter(lambda x: x != '', [
        countshow(rs.untracked_files, "new files", "new file"),
        countshow(rs.uncommited_changes, "uncommitted changes",
                  "uncommitted change"),
        countshow(rs.unpushed_commits, "commits need push", "commit needs push"),
        countshow(rs.unmerged_commits, "commits behind upstream",
                  "commit behind upstream"),
    ])
    if rs.untracked_files or rs.uncommited_changes:
        color = 'uncommited'
    elif rs.unpushed_commits:
        color = 'push-needed'
    elif rs.unmerged_commits:
        color = 'merge-needed'
    else:
        color = None
    return separator.join(parts), color


def repo_status_cells(r, separator='\n'):
    """compute the (text, color-name) status cells for a single repository.

The columns match the header used by the 'st' command: the repository name and
a single combined status column (see status_summary). The name cell has no
color (None); pass separator='\\n' for a stacked multi-line status cell or e.g.
', ' for a one-line one.
"""
    return [(r.name, None), status_summary(r.status(), separator)]
