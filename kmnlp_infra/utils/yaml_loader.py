"""YAML configuration loader with environment variable support.

This module provides utilities for loading YAML configuration files with
support for environment variable substitution using the !env tag and
loading external YAML files using the !from tag.

Example:
    In a YAML file:
        database:
            host: !env DATABASE_HOST
            port: 5432
        config: !from other_config.yaml

    Usage:
        config = load_yaml(Path("config.yaml"))
        # Returns parsed YAML with environment variables resolved and
        # external YAML files loaded
"""

import os
from pathlib import Path
from typing import IO

import yaml


class _EnvVarLoader(yaml.SafeLoader):
    """Custom YAML loader that supports !env and !from tags.

    Attributes:
        base_path: The directory path of the YAML file being loaded,
                   used to resolve relative paths in !from tags.
    """

    def __init__(self, stream: str | bytes | IO[str] | IO[bytes]) -> None:
        """Initialize the loader with a base path."""
        super().__init__(stream)
        self.base_path = Path()


def _env_constructor(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> str:
    """Construct a value from an environment variable."""
    env_var_name = loader.construct_scalar(node)
    assert isinstance(env_var_name, str)  # noqa: S101

    value = os.environ.get(env_var_name)
    if value is None:
        raise ValueError(f"Environment variable '{env_var_name}' is not set")

    return value


SimpleType = str | int | float | bool | None
ListSimpleType = list[str] | list[int] | list[float] | list[bool]

SimpleDict = dict[str, "SimpleDict | SimpleType | list[SimpleDict] | ListSimpleType"]


def _from_constructor(loader: _EnvVarLoader, node: yaml.ScalarNode) -> SimpleDict:
    """Construct a value by loading from an external YAML file.

    The path is resolved relative to the directory of the YAML file being loaded.
    """
    file_path_str = loader.construct_scalar(node)
    assert isinstance(file_path_str, str)  # noqa: S101

    # Resolve the path relative to the base YAML file's directory
    absolute_path = loader.base_path / file_path_str

    if not absolute_path.exists():
        raise FileNotFoundError(f"YAML file not found: {absolute_path}")

    # Load the referenced YAML file using the same loader
    # Set the base_path for nested !from references
    with absolute_path.open() as f:
        nested_loader = _EnvVarLoader(f)
        nested_loader.base_path = absolute_path.parent
        return nested_loader.get_single_data()


# Register the constructors
_EnvVarLoader.add_constructor("!env", _env_constructor)
_EnvVarLoader.add_constructor("!from", _from_constructor)


def load_yaml(file_path: Path) -> SimpleDict:
    """Load a YAML file with support for !env and !from tags.

    The !env tag reads environment variables.
    The !from tag loads content from another YAML file (relative path).

    Args:
        file_path: Path to the YAML file to load

    Returns:
        The parsed YAML data structure
    """
    with file_path.open() as f:
        loader = _EnvVarLoader(f)
        loader.base_path = file_path.parent
        return loader.get_single_data()  # type: ignore[return-value]
