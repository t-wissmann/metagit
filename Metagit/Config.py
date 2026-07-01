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
