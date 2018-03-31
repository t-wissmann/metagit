#!/usr/bin/env python3
import os
import sys
import re
import configparser
import subprocess

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
        self.uncommited_changes = 0
        self.unpushed_commits = 0
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

    def call(self, *args, stdout=None, may_fail=False):
        git_cmd = [
            'git',
            '--work-tree=' + self.path,
            '--git-dir=' + os.path.join(self.path, '.git'),
        ]
        git_cmd += list(args)
        debug('calling', ' '.join(git_cmd))
        proc = subprocess.Popen(git_cmd, stdout = stdout)
        out,_ = proc.communicate()
        if not out is None:
            out = out.decode()
        exit_code = proc.wait()
        if may_fail:
            return exit_code, out
        if exit_code != 0:
            raise UserMessage('Command {} failed with exit code {}'.format(\
                ' '.join(git_cmd), exit_code))
        return out

    def fetch(self):
        if os.path.isdir(self.path):
            # fetch
            self.call('fetch')
        else:
            # clone
            if not 'origin' in self.config:
                raise RepoMessage(self, 'Can not run \'git clone\', because origin is unset.')
            origin = self.config['origin']
            cmd = ['git', 'clone', origin, self.path]
            git = subprocess.Popen(cmd)
            exit_code = git.wait()
            if exit_code != 0:
                raise UserMessage("Command {} failed with exit code {}".format(\
                    ' '.join(cmd), exit_code), repo = self)

    @staticmethod
    def create(path = '.'):
        git = GitRepository(tilde_encode(path), {})
        branch = git.call('rev-parse', '--abbrev-ref', 'HEAD',\
            stdout=subprocess.PIPE).rstrip('\n')
        if branch != 'master':
            git.config['branch'] = branch
        remote_name = git.call('config', 'branch.{}.remote'.format(branch),\
                stdout=subprocess.PIPE).rstrip('\n')
        git.config['origin'] = git.call('remote', 'get-url', remote_name,\
                stdout=subprocess.PIPE).rstrip('\n')
        return git

    def status(self):
        p = self.path
        if not os.path.isdir(p):
            return RepoStatus.nonExistent()
        else:
            rs = RepoStatus()
            rs.uncommited_changes = len( \
                self.call('status', '--porcelain=1', stdout=subprocess.PIPE) \
                .split('\n')) - 1
            return rs

class Config:
    def __init__(self):
        self.config = configparser.ConfigParser()
        self.repo_objects = {}

    @staticmethod
    def filepath():
        home = os.environ['HOME']
        config_dir = os.environ.get('XDG_CONFIG_DIR', os.path.join(home, '.config'))
        return os.path.join(config_dir, 'metagit', 'config.ini')

    def reload(self):
        configfile = Config.filepath()
        if os.path.isfile(configfile):
            self.config.read(configfile)
            self.build_repo_objects()
        else:
            print("no config found")

    def build_repo_objects(self):
        self.repo_objects = {}
        for path in self.config.sections():
            self.repo_objects[path] = GitRepository(path, self.config[path])

class Main:
    def __init__(self, argv):
        self.cmd_dict = {
            'add': Main.add,
            'st': Main.status,
            'status': Main.status,
            'fetch': Main.fetch,
            'help': Main.help,
        }
        self.c = Config()
        self.c.reload()
        if len(sys.argv) >= 2:
            cmd = argv[1]
            if cmd in self.cmd_dict:
                method = self.cmd_dict[cmd]
                try:
                    method(self, argv[2:])
                except Exception as e:
                    print("Error: {}".format(str(e)))
            else:
                print("Unknown command \"{}\".".format(cmd))
                self.help([], file=sys.stderr)
        else:
            self.status([])


    def help(self, argv, file=sys.stdout):
        """print this help"""
        print("Usage: {} SUBCMD [ARGS]".format(sys.argv[0]), file=file)
        print("", file=file)
        print("Call the specified SUBCMD, which is one of the following:", file=file)
        print("", file=file)
        for cmd,method in self.cmd_dict.items():
            helpstring = method.__doc__.split('\n', 1)[0]
            print("  {:10s} {}".format(cmd, helpstring), file=file)
        print("", file=file)

    def add(self, argv):
        """add a new repository

        If no path is supplied, add the present git repository.
        """
        dry_run = False
        commit_new_config = not dry_run
        g = GitRepository.create()
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

    def fetch(self, argv):
        """update all repositories"""
        repos = self.c.repo_objects
        for p,r in repos.items():
            r.fetch()

    def status(self, argv):
        """list the status for the managed repositories"""
        repos = self.c.repo_objects
        table = [
            [ "repository\nname", "", "commit\nneeded", "push\nneeded" ]
        ]
        for p,r in repos.items():
            rs = r.status()
            uncommited_changes = ""
            if rs.uncommited_changes == 1:
                uncommited_changes = "1 change"
            elif rs.uncommited_changes > 1:
                uncommited_changes = "{} changes".format(rs.uncommited_changes)
            table.append([
                r.name,
                "not present" if not rs.exists else "",
                uncommited_changes,
            ])
        pretty_print_table(table)

Main(sys.argv)

