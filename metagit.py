#!/usr/bin/env python3
import os
import sys
import argparse
import shutil
import yaml

from src import utils
from src.utils import (
    UserMessage,
    ask,
    detect_git,
    pretty_print_table,
    repo_status_cells,
)
from src.Repository import (
    Config,
    GitRepository,
    CreateRepositoryConfig,
    config_to_repo_entry,
    repositories_in_filesystem,
)
from src.ui import run_ui


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
            utils.set_verbose(True)
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
        path = '.'
        git_root = detect_git(path)
        if git_root is None:
            raise UserMessage('{} not part of a git repository'.format( \
                os.path.abspath(path)))
        g = CreateRepositoryConfig(git_root)
        filepath = self.c.filepath()
        entry = config_to_repo_entry(g.config)
        if dry_run:
            yaml.safe_dump({'repositories': {g.tilde_path: entry}},
                           sys.stdout, sort_keys=False, default_flow_style=False)
            return
        # add the new repository and write the whole config file back
        self.c.repositories()[g.tilde_path] = entry
        self.c.save()
        if os.path.islink(filepath):
            filepath = os.readlink(filepath)
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
            table.append(repo_status_cells(r))
        pretty_print_table(table)

    def ui(self, argv):
        """interactive ncurses UI showing the repository status

Navigate the scrollable table with j/k (or the arrow keys). Press f to
fetch and P to push the selected repository, and q to quit.
"""
        run_ui(self.c.repo_objects)


Main()
