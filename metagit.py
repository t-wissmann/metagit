#!/usr/bin/env python3
import os
import sys
import configparser

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
        """add a new repository"""
        pass

    def status(self, argv):
        """list the status for the managed repositories"""
        repos = self.c.repo_objects
        for p,r in repos.items():
            print("{}: {}".format(r.name, r.status()))

Main(sys.argv)

