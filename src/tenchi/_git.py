"""Repository-local Git process configuration for verification operations."""

from __future__ import annotations

import os

_GIT_REPOSITORY_ENVIRONMENT = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_REPLACE_REF_BASE",
    "GIT_SHALLOW_FILE",
    "GIT_WORK_TREE",
    "GIT_CONFIG",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_SYSTEM",
)


def git_command(*arguments: str) -> list[str]:
    """Return a Git command that cannot invoke a configured fsmonitor hook."""
    return [
        "git",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        *arguments,
    ]


def git_environment() -> dict[str, str]:
    """Return an environment bound to the repository selected by ``cwd``."""
    environment = dict(os.environ)
    for name in _GIT_REPOSITORY_ENVIRONMENT:
        environment.pop(name, None)
    for name in tuple(environment):
        if name.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            environment.pop(name)
    environment["GIT_LITERAL_PATHSPECS"] = "1"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    return environment


__all__ = ["git_command", "git_environment"]
