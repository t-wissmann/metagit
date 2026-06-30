"""Repository models and the on-disk configuration that ties them together.

Holds the git/git-svn repository classes, their status reporting, the helpers
that discover and describe repositories in the filesystem, and the Config
object that loads them from the metagit config file.
"""
import os
import sys
import subprocess
import configparser

from .utils import UserMessage, debug, warning, tilde_encode


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
            raise UserMessage('Can not run \'git clone\', because origin is unset.', self)
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
            raise UserMessage('Can not run \'git svn clone\', ' \
                              + 'because origin is unset.', self)
        origin = self.config['origin']
        branch = self.main_branch()
        if branch != 'master':
            raise UserMessage('\'git svn clone\' only works for branch master.', self)
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
