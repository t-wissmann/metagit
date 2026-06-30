# metagit

Yet another tool to manage multiple git repositories.

## Usage
The main purposes of metagit are:

  - Keep track of git repositories that you want to have on every machine
  - Have an overview whether there is a repository with un-commited
    changes or non-pushed commits
  - Fetch from upstream for all your repositories at once (e.g. before working
    offline for some time)

## Installation
The dependencies are git, the python 3 standard libraries and
[PyYAML](https://pyyaml.org/) (for reading the config file).

## Configuration
metagit reads `$XDG_CONFIG_HOME/metagit/config.yaml` (defaulting to
`~/.config/metagit/config.yaml`). Each entry under `repositories` maps a
repository path to either its upstream url, or a mapping with the keys `url`,
`branch` (defaults to the repository's auto-detected main branch) and `type`
(`git` or `git-svn`):

```yaml
repositories:
  ~/git/metagit: git@github.com:t-wissmann/metagit
  ~/git/herbstluftwm:
    url: git@github.com:herbstluftwm/herbstluftwm
    branch: winterbreeze
```

## Credits
The main inspiration is https://github.com/stettberger/metagit

[//]: # (vim: tw=80)
