import os
import sys
from pathlib import Path


def get_app_config_dir() -> Path:
    """Returns the platform-specific user application configuration directory."""
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata)
        return Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    else:
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            return Path(xdg_config)
        return Path.home() / ".config"


class PlatformSpecificValue:
    """A class to hold values specific to different platforms."""

    def __init__(
        self, platform_values=None, per_platform_condition=None, default_value=None
    ):
        self.default_value = default_value
        self.per_platform_condition = per_platform_condition or {}
        self.platform_values = platform_values or {}

    def get_platform(self):
        plat = sys.platform
        if os.name == "posix" and plat != "darwin":
            return "linux"
        return plat

    def set(self, value):
        """Set a value for a specific platform."""
        self.platform_values[self.get_platform()] = value

    def get(self):
        """Get the value for a specific platform, or the default if not set."""
        cond = self.per_platform_condition.get(self.get_platform())
        if cond and not cond():
            return None
        return self.platform_values.get(self.get_platform(), self.default_value)


class PlatformSpecificLazyValue(PlatformSpecificValue):
    """A class to hold lazily evaluated values specific to different platforms."""

    def __init__(self, platform_values=None, default_value=None):
        super().__init__(platform_values=platform_values, default_value=default_value)

    def get(self):
        """Get the value for a specific platform by calling its factory, or the default if not set."""
        value_factory = super().get()
        if value_factory is not None:
            return value_factory()
        return None

    def __call__(self):
        return self.get()
