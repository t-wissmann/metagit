#!/usr/bin/env python3
import os
import sys
import re
import configparser
import subprocess
import argparse
import shutil

class UserMessage(Exception):
    def __init__(self, msg, repo=None):
        self.msg = msg
        self.repo = repo
    def __str__(self):
        return self.msg

debug_messages = False
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

class RepoStatus:
    def __init__(self):
        self.exists = True
        self.untracked_files = 0
        self.uncommited_changes = 0
        self.unpushed_commits = 0
        self.unmerged_commits = 0
        pass

    @staticmethod
    def nonExistent():
        rs = RepoStatus()
        rs.exists = False
        return rs
    def __str__(self):
        if not self.exists:
            return "does not exist"
        else:
            msg = ""
            msg += "uncommited " if self.uncommited_changes > 0 else "clean "
            msg += "unpushed " if self.unpushed_commits > 0 else "fully published"
            return msg

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

class GitRepository:
    def __init__(self, tilde_path, config):
        self.tilde_path = tilde_path
        self.path = os.path.expanduser(tilde_path)
        self.config = config # dict of settings
        self.name = os.path.basename(self.path)

    def fingerprint(self):
        # get the core config options
        # if the fingerprint of two repos match, then it is likely
        # that they are the same repository
        return tuple(map(lambda a: self.config.get(a, None), \
                ('type', 'branch', 'origin')))

    def call(self, *args, stdout=None, stderr=subprocess.PIPE, may_fail=False):
        git_cmd = [
            'git',
        ]
        if args[0] != 'clone' and args[0:2] != ('svn','clone'):
            # passing these options to clone will make
            # the repo lack the .git dir
            git_cmd += [
                '--work-tree=' + self.path,
                '--git-dir=' + os.path.join(self.path, '.git'),
            ]
        git_cmd += list(args)
        debug('calling', ' '.join(git_cmd))
        proc = subprocess.Popen(git_cmd, stdout = stdout, \
                                stderr = stderr)
        out,err = proc.communicate()
        if not out is None:
            out = out.decode()
        exit_code = proc.wait()
        if may_fail:
            return exit_code, out
        if exit_code != 0:
            err_str = "" if err is None else err.decode().rstrip('\n')
            raise UserMessage('Command »{}« failed with exit code {}: »{}«'.format(\
                ' '.join(git_cmd), exit_code, err_str))
        else:
            if not err is None:
                print(err.decode(), end='', file=sys.stderr)
        return out

    def exists(self):
        return os.path.isdir(self.path)

    def fetch(self):
        if self.exists():
            # fetch
            self.call('fetch')

    def push(self):
        if self.exists():
            self.call('push', stderr=None)

    def clone(self):
        if self.exists():
            return # nothing to do
        if not 'origin' in self.config:
            raise RepoMessage(self, 'Can not run \'git clone\', because origin is unset.')
        origin = self.config['origin']
        branch = self.main_branch()
        self.call('clone', '-b', branch, origin, self.path, stderr=None)

    def main_branch(self):
        return self.config.get('branch', 'master')

    def upstream_branch(self):
        return '@{u}'

    def detect_upstream_svn_url(self):
        exit_code,svn_remote_url = self.call('config', \
                'svn-remote.svn.url', \
                stdout=subprocess.PIPE, may_fail=True)
        if exit_code == 0:
            # detecting a svn remote
            return svn_remote_url.rstrip('\n')
        else:
            return None

    def detect_upstream_url(self, branch = None):
        if branch is None:
            branch = self.main_branch()
        exit_code,remote_name = self.call('config', 'branch.{}.remote'.format(branch),\
                stdout=subprocess.PIPE, may_fail = True)
        if exit_code != 0:
            return None
        exit_code,url = self.call('remote', 'get-url', remote_name.rstrip('\n'),\
                stdout=subprocess.PIPE, may_fail = True)
        if exit_code != 0:
            return None
        return url.rstrip('\n')

    def __str__(self):
        return self.tilde_path

    def status(self):
        p = self.path
        if not os.path.isdir(p):
            return RepoStatus.nonExistent()
        else:
            rs = RepoStatus()
            git_status_lines = self.call('status', '--porcelain=1', stdout=subprocess.PIPE).splitlines()
            for line in git_status_lines:
                if line[0:2] == '??':
                    rs.untracked_files += 1
                else:
                    rs.uncommited_changes += 1
            try:
                rs.unmerged_commits = len( \
                    self.call('log', '--format=format:X', \
                        self.main_branch() + '..' + self.upstream_branch(), \
                    stdout=subprocess.PIPE) \
                    .replace('\n', ''))
                rs.unpushed_commits = len( \
                    self.call('log', '--format=format:X', \
                         self.upstream_branch() + '..' + self.main_branch(), \
                    stdout=subprocess.PIPE) \
                    .replace('\n', ''))
            except Exception as e:
                warning("Warning: Can not count commits: {}".format(e))
            return rs

class GitSvnRepository(GitRepository):
    def __init__(self, tilde_path, config):
        super().__init__(tilde_path, config)

    def upstream_branch(self):
        return 'git-svn'

    def fetch(self):
        if self.exists():
            # fetch
            self.call('svn', 'fetch')

    def push(self):
        if self.exists():
            self.call('svn', 'dcommit', stderr=None)

    def clone(self):
        if self.exists():
            return # nothing to do
        if not 'origin' in self.config:
            raise RepoMessage(self, 'Can not run \'git svn clone\', ' \
                                    + 'because origin is unset.')
        origin = self.config['origin']
        branch = self.main_branch()
        if branch != 'master':
            raise RepoMessage(self, '\'git svn clone\' only works for branch master.')
        self.call('svn', 'clone', origin, self.path, stderr=None)


def CreateRepositoryConfig(path = '.', needs_origin = True):
    git = GitRepository(tilde_encode(path), {})
    exit_code, branch = git.call('rev-parse', '--abbrev-ref', 'HEAD',\
        stdout=subprocess.PIPE, may_fail = True)
    if exit_code != 0:
        branch = 'master'
    else:
        branch = branch.rstrip('\n')
    if branch != 'master':
        git.config['branch'] = branch
    svn_url = git.detect_upstream_svn_url()
    if not svn_url is None:
        git.config['origin'] = svn_url
        git.config['type'] = 'git-svn'
    else:
        # ordinary git
        url = git.detect_upstream_url()
        if needs_origin and url is None:
            raise UserMessage('Could not detect an upstream url')
        git.config['origin'] = url
    return git

def locate_git_repositories():
    cmd = ['locate', '-0', '-b', '\\.git' ]
    proc = subprocess.Popen(cmd,
                            stdout=subprocess.PIPE)
    stdout, _ = proc.communicate()
    status = proc.wait()
    if status != 0:
        return []
    res = []
    for l in stdout.decode().split('\0'):
        head, tail = os.path.split(l)
        if tail != '.git':
            continue
        res.append(head)
    return res

def repositories_in_filesystem():
    # return a dictionary of all git repos in the file system
    if hasattr(repositories_in_filesystem, 'dict'):
        return repositories_in_filesystem.dict
    print("Searching for repositories in the entire file system...", file=sys.stderr)
    located_repos = { }
    for p in locate_git_repositories():
        r = CreateRepositoryConfig(p, needs_origin = False)
        fp = r.fingerprint()
        located_repos[fp] = r
    repositories_in_filesystem.dict = located_repos
    return repositories_in_filesystem.dict

class Config:
    def __init__(self):
        self.config = configparser.ConfigParser()
        self.repo_objects = {}

    @staticmethod
    def filepath():
        home = os.environ['HOME']
        config_dir = os.environ.get('XDG_CONFIG_HOME', os.path.join(home, '.config'))
        return os.path.join(config_dir, 'metagit', 'config.ini')

    def reload(self):
        configfile = Config.filepath()
        if os.path.isfile(configfile):
            self.config.read(configfile)
            self.build_repo_objects()
        else:
            print("no config found")

    def save(self):
        with (open(self.filepath(), 'w')) as filehandle:
            self.config.write(filehandle)

    def build_repo_objects(self):
        self.repo_objects = {}
        for path in self.config.sections():
            repo_type = self.config[path].get('type', 'git')
            obj = None
            classes = {
                'git': GitRepository,
                'git-svn': GitSvnRepository,
            }
            if repo_type in classes:
                self.repo_objects[path] = \
                    classes[repo_type](path, self.config[path])
            else:
                raise UserMessage('Error in section {}: unknown type \'{}\''\
                    .format(path, repo_type))

class Main:
    def __init__(self):
        # maps a command name to a tuple (callback, add_arguments), where
        # add_arguments is an optional callable registering command specific
        # arguments on the command's subparser (or None for none)
        self.cmd_dict = {
            'add': (Main.add, lambda sub: sub.add_argument(
                '-n', '--dry-run', action='store_true',
                help='dry run: only print config')),
            'clone': (Main.clone, None),
            'st': (Main.status, None),
            'status': (Main.status, None),
            'ui': (Main.ui, None),
            'fetch': (Main.fetch, lambda sub: sub.add_argument(
                '-c', '--clone', action='store_true',
                help='clone repository if it does not exist locally')),
        }
        self.c = Config()
        try:
            self.c.reload()
        except UserMessage as e:
            print("Error while loading config {}:\n{}"\
                .format(self.c.filepath(), e))
            sys.exit(1)
        self.parser = self.build_parser()
        parsed = self.parser.parse_args()
        if parsed.verbose:
            global debug_messages
            debug_messages = True
        method = getattr(parsed, 'func', Main.status)
        try:
            res = method(self, parsed)
        except UserMessage as e:
            print("Error: {}".format(str(e)))
            res = 1
        except KeyboardInterrupt:
            print("Interrupted.", file=sys.stderr)
            res = 1
        if res is not None:
            sys.exit(res)

    def build_parser(self):
        # the global options are shared by the top-level parser and every
        # subparser, so e.g. -v may be passed before or after the SUBCMD
        global_parser = argparse.ArgumentParser(add_help=False)
        global_parser.add_argument('-v', '--verbose', action='store_true',
                                   help='activate verbose output')
        parser = argparse.ArgumentParser(
            parents=[global_parser],
            description='Manage a collection of git repositories.')
        subparsers = parser.add_subparsers(dest='command', metavar='SUBCMD')
        for name, (method, add_arguments) in self.cmd_dict.items():
            doc = method.__doc__ or ''
            sub = subparsers.add_parser(
                name,
                parents=[global_parser],
                help=doc.split('\n', 1)[0],
                description=doc,
                formatter_class=argparse.RawDescriptionHelpFormatter)
            sub.set_defaults(func=method)
            if add_arguments is not None:
                add_arguments(sub)
        return parser

    def add(self, argv):
        """add a new repository"""
        dry_run = argv.dry_run
        commit_new_config = not dry_run
        path = '.'
        git_root = detect_git(path)
        if git_root is None:
            raise UserMessage('{} not part of a git repository'.format( \
                os.path.abspath(path)))
        g = CreateRepositoryConfig(git_root)
        filepath = self.c.filepath()
        new_conf = configparser.ConfigParser()
        new_conf[g.tilde_path] = g.config
        with (sys.stdout if dry_run else open(filepath, 'a')) as filehandle:
            new_conf.write(filehandle)
        if os.path.islink(filepath):
            filepath = os.readlink(filepath)
        if commit_new_config:
            # detect the git repository handling the config
            git_path = detect_git(os.path.dirname(filepath))
            if git_path is None:
                print("Config file {} not managed in a git, not committing anything"\
                        .format(filepath))
            else:
                print("Committing changes to the git at {}".format(git_path))
                config_repo = GitRepository(git_path, {})
                msg = 'Add git ' + g.name
                config_repo.call('commit', '-m', msg, '--', filepath)

    def clone(self, argv):
        """clone non-existing repositories

If a non-existing repository can be found in the filesystem already (using
locate), then the directory is simply moved (after confirmation).
"""
        repos = self.c.repo_objects

        for p,r in repos.items():
            if r.exists():
                print("{} exists".format(r.tilde_path))
            else:
                print("{} does not exist".format(r.tilde_path))
                all_repos = repositories_in_filesystem()
                loc_r = all_repos.get(r.fingerprint(), None)
                if not loc_r is None and ask('Move {} to {}?'.format(loc_r.tilde_path, p)):
                    parent = os.path.dirname(r.path.rstrip('/'))
                    os.makedirs(parent, exist_ok = True)
                    shutil.move(loc_r.path, r.path)
                elif ask('Clone {}?'.format(p)):
                    r.clone()

    def fetch(self, argv):
        """update all repositories"""
        clone_if_necessary = argv.clone
        repos = self.c.repo_objects
        total = len(repos)
        idx = 0
        for p, r in repos.items():
            idx += 1
            if r.exists():
                print(f"({idx}/{total}) Fetching {r.tilde_path}", file=sys.stderr)
                r.fetch()
            else:
                if clone_if_necessary:
                    r.clone()
                else:
                    print("{} does not exist".format(r.tilde_path))

    @staticmethod
    def repo_status_cells(r, separator='\n'):
        """compute the status cells for a single repository

The columns match the header used by the 'st' command:
name, presence, uncommited changes, push needed, merge needed.
"""
        rs = r.status()
        return [
            r.name,
            "not present" if not rs.exists else "",
            separator.join(filter(lambda x: x != '', [
                countshow(rs.untracked_files, "new files", "new file"),
                countshow(rs.uncommited_changes, "changes", "change"),
            ])),
            countshow(rs.unpushed_commits, "commits", "commit"),
            countshow(rs.unmerged_commits, "commits", "commit"),
        ]

    def status(self, argv):
        """list the status for the managed repositories"""
        repos = self.c.repo_objects
        table = [
            [ "repository\nname",
              "",
              "uncommited\nchanges",
              "push\nneeded",
              "merge\nneeded",
            ]
        ]
        for p,r in repos.items():
            table.append(Main.repo_status_cells(r))
        pretty_print_table(table)

    def ui(self, argv):
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
        repos = self.c.repo_objects
        if not repos:
            raise UserMessage("No repositories are configured")
        rows = []
        for p, r in repos.items():
            rows.append({'repo': r, 'cells': Main.repo_status_cells(r, ', ')})
        curses.wrapper(self._ui_main, rows)

    def _ui_main(self, stdscr, rows):
        import curses
        curses.curs_set(0)
        header = ["repository", "", "uncommited", "push needed", "merge needed"]
        footer = "j/↓: down  k/↑: up  f: fetch  P: push  q: quit"
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
                self._ui_action(stdscr, rows, sel, 'fetch')
            elif ch == ord('P'):
                self._ui_action(stdscr, rows, sel, 'push')

    def _ui_action(self, stdscr, rows, sel, action):
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
        rows[sel]['cells'] = Main.repo_status_cells(r, ', ')
        stdscr.clear()
        stdscr.refresh()

Main()

