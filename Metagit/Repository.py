"""Repository models and the on-disk configuration that ties them together.

Holds the git/git-svn repository classes, their status reporting, the helpers
that discover and describe repositories in the filesystem, and the Config
object that loads them from the metagit config file.
"""
import os
import sys
import copy
import subprocess
import yaml

from .utils import UserMessage, debug, warning, tilde_encode


# the configuration used as a starting point; the user's config file is merged
# on top of it in Config.reload()
DEFAULT_CONFIG = {
    'repositories': {},
    'keys': {
        '↓': 'down',
        'j': 'down',
        '↑': 'up',
        'k': 'up',
        'f': 'run-bg git fetch',
        'P': 'run-fg git push',
        'r': 'refresh',
        'q': 'quit',
        'Enter': 'run-fg $SHELL',
    },
}


def deep_merge(base, override):
    """recursively merge `override` into `base`, mutating and returning `base`.

    Nested mappings are merged key by key; any other value in `override`
    replaces the one in `base`.
    """
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


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
                ('type', 'branch', 'url')))

    def call(self, *args, stdout=None, stderr=subprocess.PIPE, may_fail=False,
             quiet=False, shell=False):
        """run a command with the repository as the working directory.

        With shell=False (the default) `args` is the full argument list of a
        command (e.g. 'git', 'fetch'). With shell=True a single command string
        is run through the shell, which is convenient for user-provided
        commands. Either way it runs inside the repository, so git picks up the
        working tree on its own. When the directory does not exist yet (e.g.
        'git clone', which creates it and is given an explicit destination) the
        command runs from the current working directory instead.
        """
        cmd = args[0] if shell else list(args)
        cmd_str = cmd if shell else ' '.join(cmd)
        cwd = self.path if os.path.isdir(self.path) else None
        debug('calling', cmd_str, 'in', cwd if cwd else '.')
        proc = subprocess.Popen(cmd, stdout = stdout, \
                                stderr = stderr, cwd = cwd, shell = shell)
        out,err = proc.communicate()
        if not out is None:
            out = out.decode()
        exit_code = proc.wait()
        if may_fail:
            return exit_code, out
        if exit_code != 0:
            err_str = "" if err is None else err.decode().rstrip('\n')
            raise UserMessage('Command »{}« failed with exit code {}: »{}«'.format(\
                cmd_str, exit_code, err_str))
        else:
            if not err is None and not quiet:
                print(err.decode(), end='', file=sys.stderr)
        return out

    def exists(self):
        return os.path.isdir(self.path)

    def fetch(self, quiet=False):
        if self.exists():
            # fetch
            self.call('git', 'fetch', quiet=quiet)

    def push(self):
        if self.exists():
            self.call('git', 'push', stderr=None)

    def clone(self):
        if self.exists():
            return # nothing to do
        if not 'url' in self.config:
            raise UserMessage('Can not run \'git clone\', because url is unset.', self)
        origin = self.config['url']
        args = ['clone']
        if 'branch' in self.config:
            # honour an explicit override; otherwise let git check out
            # whatever the remote advertises as its default branch
            args += ['-b', self.config['branch']]
        args += [origin, self.path]
        self.call('git', *args, stderr=None)

    def main_branch(self):
        if 'branch' in self.config:
            return self.config['branch']
        if not hasattr(self, '_detected_main_branch'):
            self._detected_main_branch = self.detect_main_branch()
        return self._detected_main_branch

    def detect_main_branch(self):
        """detect a repository's default branch.

        Used as the fallback when no explicit 'branch' is configured. For an
        existing checkout we read the remote's advertised default branch
        (origin/HEAD, as set up by 'git clone'); when that is unavailable we
        fall back to 'master'.
        """
        if self.exists():
            exit_code, head = self.call('git', 'symbolic-ref', '--short', \
                'refs/remotes/origin/HEAD', \
                stdout=subprocess.PIPE, may_fail=True)
            if exit_code == 0:
                # e.g. 'origin/main' -> 'main'
                return head.rstrip('\n').split('/', 1)[-1]
        return 'master'

    def upstream_branch(self):
        return '@{u}'

    def detect_upstream_svn_url(self):
        exit_code,svn_remote_url = self.call('git', 'config', \
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
        exit_code,remote_name = self.call('git', 'config', \
                'branch.{}.remote'.format(branch),\
                stdout=subprocess.PIPE, may_fail = True)
        if exit_code != 0:
            return None
        exit_code,url = self.call('git', 'remote', 'get-url', \
                remote_name.rstrip('\n'),\
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
            git_status_lines = self.call('git', 'status', '--porcelain=1', stdout=subprocess.PIPE).splitlines()
            for line in git_status_lines:
                if line[0:2] == '??':
                    rs.untracked_files += 1
                else:
                    rs.uncommited_changes += 1
            try:
                rs.unmerged_commits = len( \
                    self.call('git', 'log', '--format=format:X', \
                        self.main_branch() + '..' + self.upstream_branch(), \
                    stdout=subprocess.PIPE) \
                    .replace('\n', ''))
                rs.unpushed_commits = len( \
                    self.call('git', 'log', '--format=format:X', \
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

    def fetch(self, quiet=False):
        if self.exists():
            # fetch
            self.call('git', 'svn', 'fetch', quiet=quiet)

    def push(self):
        if self.exists():
            self.call('git', 'svn', 'dcommit', stderr=None)

    def clone(self):
        if self.exists():
            return # nothing to do
        if not 'url' in self.config:
            raise UserMessage('Can not run \'git svn clone\', ' \
                              + 'because url is unset.', self)
        origin = self.config['url']
        branch = self.main_branch()
        if branch != 'master':
            raise UserMessage('\'git svn clone\' only works for branch master.', self)
        self.call('git', 'svn', 'clone', origin, self.path, stderr=None)


def CreateRepositoryConfig(path = '.', needs_origin = True):
    git = GitRepository(tilde_encode(path), {})
    exit_code, branch = git.call('git', 'rev-parse', '--abbrev-ref', 'HEAD',\
        stdout=subprocess.PIPE, may_fail = True)
    if exit_code == 0:
        branch = branch.rstrip('\n')
        # only record the branch when it deviates from the repository's
        # auto-detected default, so we don't pin e.g. 'main' needlessly
        if branch != git.detect_main_branch():
            git.config['branch'] = branch
    svn_url = git.detect_upstream_svn_url()
    if not svn_url is None:
        git.config['url'] = svn_url
        git.config['type'] = 'git-svn'
    else:
        # ordinary git
        url = git.detect_upstream_url()
        if needs_origin and url is None:
            raise UserMessage('Could not detect an upstream url')
        git.config['url'] = url
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


def repo_entry_to_config(path, entry):
    """normalize a single 'repositories' entry into a settings dict

A bare string is shorthand for the upstream url; a mapping is taken verbatim
(keys such as 'url', 'branch' and 'type').
"""
    if isinstance(entry, str):
        return {'url': entry}
    elif isinstance(entry, dict):
        return dict(entry)
    else:
        raise UserMessage('Error in entry {}: expected a url string or a '
                          'mapping, got {}'.format(path, type(entry).__name__))


def config_to_repo_entry(config):
    """render a settings dict back into its compact 'repositories' entry

Collapses to a plain url string when no other options are set.
"""
    if set(config.keys()) == {'url'}:
        return config['url']
    return dict(config)


class Config:
    def __init__(self):
        self.data = {}
        self.repo_objects = {}

    @staticmethod
    def filepath():
        home = os.environ['HOME']
        config_dir = os.environ.get('XDG_CONFIG_HOME', os.path.join(home, '.config'))
        return os.path.join(config_dir, 'metagit', 'config.yaml')

    def reload(self):
        # start from the default configuration and merge the user's config
        # file (if any) on top of it, so absent sections fall back to their
        # defaults (e.g. an empty repository list and the default key bindings)
        self.data = copy.deepcopy(DEFAULT_CONFIG)
        configfile = Config.filepath()
        if os.path.isfile(configfile):
            with open(configfile) as filehandle:
                user_data = yaml.safe_load(filehandle) or {}
            if not isinstance(user_data, dict):
                raise UserMessage('Config must be a mapping at the top level')
            deep_merge(self.data, user_data)
        self.build_repo_objects()

    def keys(self):
        """the mapping of key to action for the interactive UI"""
        return self.data.get('keys', {})

    def repositories(self):
        """the (mutable) mapping of repository path to its config entry"""
        repos = self.data.get('repositories')
        if repos is None:
            repos = {}
            self.data['repositories'] = repos
        return repos

    def save(self):
        with open(self.filepath(), 'w') as filehandle:
            yaml.safe_dump(self.data, filehandle,
                           sort_keys=False, default_flow_style=False)

    def build_repo_objects(self):
        self.repo_objects = {}
        classes = {
            'git': GitRepository,
            'git-svn': GitSvnRepository,
        }
        for path, entry in self.repositories().items():
            config = repo_entry_to_config(path, entry)
            repo_type = config.get('type', 'git')
            if repo_type in classes:
                self.repo_objects[path] = classes[repo_type](path, config)
            else:
                raise UserMessage('Error in entry {}: unknown type \'{}\''\
                    .format(path, repo_type))
