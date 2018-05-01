#!/usr/bin/env python3
import os
import sys
import re
import configparser
import subprocess
import getopt

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


def CreateRepositoryConfig(path = '.'):
    path = detect_git(path)
    git = GitRepository(tilde_encode(path), {})
    branch = git.call('rev-parse', '--abbrev-ref', 'HEAD',\
        stdout=subprocess.PIPE).rstrip('\n')
    if branch != 'master':
        git.config['branch'] = branch
    exit_code,svn_remote_url = git.call('config', \
            'svn-remote.svn.url', \
            stdout=subprocess.PIPE, may_fail=True)
    if exit_code == 0:
        # detecting a svn remote
        git.config['origin'] = svn_remote_url.rstrip('\n')
        git.config['type'] = 'git-svn'
    else:
        # ordinary git
        remote_name = git.call('config', 'branch.{}.remote'.format(branch),\
                stdout=subprocess.PIPE).rstrip('\n')
        git.config['origin'] = git.call('remote', 'get-url', remote_name,\
                stdout=subprocess.PIPE).rstrip('\n')
    return git

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
    def __init__(self, argv):
        self.global_opts = {
            'v': 'activate verbose output',
            'help': '',
            'h': 'print this help',
        }
        self.cmd_dict = {
            'add': Main.add,
            'clone': Main.clone,
            'st': Main.status,
            'status': Main.status,
            'fetch': Main.fetch,
            'help': Main.help,
        }
        self.opts_dict = {
            Main.fetch: {
                'c': 'clone repository if it does not exist locally',
            },
            Main.clone: {
                'i': 'ask before cloning',
            },
            Main.add: {
                'n': 'dry run: only print config',
            },
        }
        self.c = Config()
        try:
            self.c.reload()
        except UserMessage as e:
            print("Error while loading config {}:\n{}"\
                .format(self.c.filepath(), e))
            sys.exit(1)
        self.print_help = False # if activated, only print help
        if len(sys.argv) >= 2:
            cmd = argv[1]
            if cmd in self.cmd_dict:
                method = self.cmd_dict[cmd]
                cmd_opts = self.opts_dict.get(method, {})
                res = self.run_cmd(cmd, method, cmd_opts, argv[2:])
                if not res is None:
                    sys.exit(res)
            else:
                print("Unknown command \"{}\".".format(cmd))
                self.help([], file=sys.stderr)
                sys.exit(1)
        else:
            self.status([])

    def run_cmd(self, name, method, cmd_opts, argv):
        try:
            so, lo = self.assemple_opts_from_dict(cmd_opts)
            opts, cmd_args = self.getopt(argv, so, lo)
            if self.print_help:
                self.cmd_help(name, method, cmd_opts, file=sys.stdout)
            else:
                if len(cmd_opts) > 0:
                    return method(self, cmd_args, options = opts)
                else:
                    return method(self, cmd_args)
        except getopt.GetoptError as err:
            # print help information and exit:
            print(name + ": " + str(err), file=sys.stderr)
            self.cmd_help(name, method, cmd_opts, file=sys.stderr)
            return 1
        except UserMessage as e:
            print("Error: {}".format(str(e)))
            return 1
        except KeyboardInterrupt as e:
            print("Interrupted.", file=sys.stderr)
            return 1

    def cmd_help(self, name, method, cmd_opts, file=sys.stdout):
        print("Usage: {} {} [ARGS]".format(sys.argv[0], name), file=file)
        print("", file=file)
        print(method.__doc__, file=file)
        print("", file=file)
        print("The following global options are accepted:", file=file)
        print("", file=file)
        self.print_opts_doc(self.global_opts, file=file)
        print("", file=file)
        if len(cmd_opts) > 0:
            print("Additionally, the following options are accepted:", file=file)
            print("", file=file)
            self.print_opts_doc(cmd_opts, file=file)
            print("", file=file)

    def print_opts_doc(self, cmd_opts, file=sys.stdout):
        for o,helpstring in cmd_opts.items():
            o_str = ''
            o_str = '-' if len(o.rstrip(':')) == 1 else '--'
            o_str += o.replace(':', ' ')
            if o.rstrip(':=') != o: # if the option expects a parameter
                o_str += 'X'
            print("  {:10s} {}".format(o_str, helpstring), file=file)

    def assemple_opts_from_dict(self, cmd_opts):
        shortopts = ''
        longopts = []
        for o,_ in cmd_opts.items():
            if len(o) == 1 or o[1] == ':':
                shortopts += o
            else:
                longopts.append(o)
        return shortopts, longopts

    def getopt(self, argv, shortopts, longopts):
        # add global options
        so, lo = self.assemple_opts_from_dict(self.global_opts)
        shortopts += so
        longopts += lo
        local_options = []
        opts, args = getopt.getopt(argv, shortopts, longopts)
        cmd_specific_opts = []
        for o, a in opts:
            if o == '-v':
                global debug_messages
                debug_messages = True
            elif o == '-h' or o == '--help':
                self.print_help = True
            else:
                cmd_specific_opts.append((o,a))
        return cmd_specific_opts, args


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
        print("All commands accept the following global options:", file=file)
        print("", file=file)
        self.print_opts_doc(self.global_opts, file=file)
        print("", file=file)

    def add(self, argv, options = []):
        """add a new repository"""
        dry_run = False
        for o,a in options:
            if o == '-n':
                dry_run = True
        commit_new_config = not dry_run
        g = CreateRepositoryConfig()
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

    def clone(self, argv, options):
        """clone non-existend repositories"""
        repos = self.c.repo_objects
        interactive = False
        for o,a in options:
            if o == '-i':
                interactive = True

        for p,r in repos.items():
            if r.exists():
                print("{} exists".format(r.tilde_path))
            else:
                if not interactive or ask('Clone {}?'.format(p)):
                    r.clone()

    def fetch(self, argv, options=[]):
        """update all repositories"""
        clone_if_necessary = False
        for o,a in options:
            if o == '-c':
                clone_if_necessary = True
        repos = self.c.repo_objects
        for p,r in repos.items():
            if r.exists():
                r.fetch()
            else:
                if clone_if_necessary:
                    r.clone()
                else:
                    print("{} does not exist".format(r.tilde_path))

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
        def countshow(cnt, suffix = None, suffix1 = None):
            if cnt == 0:
                return ""
            elif cnt == 1:
                return str(cnt) + ('' if suffix1 is None else ' ' + suffix1)
            else:
                return str(cnt) + ('' if suffix is None else ' ' + suffix)
        for p,r in repos.items():
            rs = r.status()
            table.append([
                r.name,
                "not present" if not rs.exists else "",
                '\n'.join(filter(lambda x: x != '', [
                    countshow(rs.untracked_files, "new files", "new file"),
                    countshow(rs.uncommited_changes, "changes", "change"),
                ])),
                countshow(rs.unpushed_commits, "commits", "commit"),
                countshow(rs.unmerged_commits, "commits", "commit"),
            ])
        pretty_print_table(table)

Main(sys.argv)

