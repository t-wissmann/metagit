#!/usr/bin/env python3
import os
import sys
import configparser
import subprocess

class UserMessage(Exception):
    def __init__(self, msg, repo=None):
        self.msg = msg
        self.repo = repo
    def __str__(self):
        return self.msg

def debug(*args):
    print(' '.join(list(args)), file=sys.stderr)

class RepoStatus:
    def __init__(self):
        self.exists = True
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
            return "exists"

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
        exit_code = proc.wait()
        if not may_fail and exit_code != 0:
            raise UserMessage('Command {} failed with exit code {}'.format(\
                ' '.join(git_cmd), exit_code))
        else:
            return exit_code, out
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


    def status(self):
        p = self.path
        if not os.path.isdir(p):
            return RepoStatus.nonExistent()
        else:
            rs = RepoStatus()
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
                method(self, argv[2:])
            else:
                print("Unknown command \"{}\".".format(cmd))
        else:
            self.help([], file=sys.stderr)


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
        pass

    def fetch(self, argv):
        """update all repositories"""
        repos = self.c.repo_objects
        for p,r in repos.items():
            r.fetch()

    def status(self, argv):
        """list the status for the managed repositories"""
        repos = self.c.repo_objects
        for p,r in repos.items():
            print("{}: {}".format(r.name, r.status()))

Main(sys.argv)

