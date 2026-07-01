"""The on-disk configuration for metagit.

Holds the default configuration, the merge helper used to layer the user's
config file on top of it, the helpers that translate between compact
'repositories' entries and settings dicts, and the Config object that loads
everything from the metagit config file.
"""
import os
import copy
import yaml

from .utils import UserMessage
from .Repository import GitRepository, GitSvnRepository


# the configuration used as a starting point; the user's config file is merged
# on top of it in Config.reload()
DEFAULT_CONFIG = {
    'repositories': {},
    # after a 'run-fg' command finishes, only prompt with 'Press enter to
    # continue...' when it ran for fewer than this many seconds (long-running
    # commands such as an interactive shell don't need a manual confirmation)
    'run-fg-prompt-threshold': 5,
    'keys': {
        '↓': 'down',
        'j': 'down',
        '↑': 'up',
        'k': 'up',
        'f': 'run-bg git fetch',
        'P': 'run-fg git push',
        'r': 'refresh',
        'd': 'detect',
        'q': 'quit',
        '?': 'help',
        'Enter': 'run-fg $SHELL',
    },
    # colors for the interactive UI, mapping a UI element to a color/attribute
    # spec. A spec is a space separated list of tokens: a foreground color
    # name, 'on <color>' for the background, and any number of attribute names
    # (bold, dim, reverse, underline, standout, blink). Color names are the
    # eight terminal colors (black, red, green, yellow, blue, magenta, cyan,
    # white) plus 'default' for the terminal's default color. Examples:
    # 'yellow', 'red bold', 'white on blue', 'default reverse'.
    'colors': {
        'header': 'bold',          # the table header row
        'selected': 'reverse',     # the currently selected repository row
        'not-present': 'red',      # repositories missing from the filesystem
        'uncommited': 'yellow',    # the 'uncommited changes' column
        'push-needed': 'cyan',     # the 'push needed' column
        'merge-needed': 'magenta', # the 'merge needed' column
        'running': 'blue',         # a background command in progress
        'failed': 'red bold',      # a background command that failed
        'detected': 'dim',         # repositories found by the 'detect' action
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

    def colors(self):
        """the mapping of UI element name to its color/attribute spec"""
        return self.data.get('colors', {})

    def run_fg_prompt_threshold(self):
        """seconds under which a finished 'run-fg' command still prompts to
        continue; commands running longer than this return to the UI directly"""
        return self.data.get('run-fg-prompt-threshold', 5)

    def repositories(self):
        """the (mutable) mapping of repository path to its config entry"""
        repos = self.data.get('repositories')
        if repos is None:
            repos = {}
            self.data['repositories'] = repos
        return repos

    def documentation(self):
        """render the effective configuration as human readable documentation.

        Describes the config file location and every setting, and lists both
        the configured key bindings and the actions they may refer to.
        """
        # imported lazily to avoid a circular import (ui imports from utils
        # only, but keeping the import here documents the dependency clearly)
        from .ui import action_docs

        lines = []
        lines.append('metagit configuration')
        lines.append('=====================')
        lines.append('')
        lines.append('Config file: {}'.format(self.filepath()))
        if not os.path.isfile(self.filepath()):
            lines.append('(the file does not exist yet; built-in defaults are '
                         'shown below)')
        lines.append('')

        lines.append('Settings')
        lines.append('--------')
        lines.append('run-fg-prompt-threshold: {} seconds'
                     .format(self.run_fg_prompt_threshold()))
        lines.append('  A finished foreground (run-fg) command only prompts')
        lines.append('  with "Press enter to continue..." when it ran for')
        lines.append('  fewer than this many seconds.')
        lines.append('')

        repos = self.repositories()
        lines.append('Managed repositories: {}'.format(len(repos)))
        for path in repos:
            lines.append('  - {}'.format(path))
        lines.append('')

        lines.append('Key bindings')
        lines.append('------------')
        keys = self.keys()
        if keys:
            width = max(len(str(k)) for k in keys)
            for key, action in keys.items():
                lines.append('  {:<{w}}  {}'.format(str(key), action, w=width))
        else:
            lines.append('  (none configured)')
        lines.append('')

        lines.append('Available actions')
        lines.append('-----------------')
        docs = action_docs()
        width = max(len(name) for name in docs)
        for name, doc in docs.items():
            lines.append('  {:<{w}}  {}'.format(name, doc, w=width))
        lines.append('')

        lines.append('Colors')
        lines.append('------')
        lines.append('A color spec is a space separated list of a foreground')
        lines.append('color name, "on <color>" for the background and any')
        lines.append('attributes (bold, dim, reverse, underline, standout,')
        lines.append('blink). Colors: black, red, green, yellow, blue, magenta,')
        lines.append('cyan, white and default.')
        colors = self.colors()
        if colors:
            width = max(len(str(name)) for name in colors)
            for name, spec in colors.items():
                lines.append('  {:<{w}}  {}'.format(str(name), spec, w=width))
        else:
            lines.append('  (none configured)')
        lines.append('')

        return '\n'.join(lines)

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
