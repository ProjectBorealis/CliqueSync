import configparser
import itertools
import os
from functools import lru_cache
from xml.etree.ElementTree import parse

from pbpy import pbtools

# Singleton Config and path to said config
config = None
config_filepath = None

user_config = None


def get(key):
    if key is None or config is None or config.get(str(key)) is None:
        pbtools.error_state(f"Invalid config get request: {key}", hush=True)
    val = config.get(str(key))
    if val == "":
        pbtools.error_state(f"{key} is not set in config", hush=True)

    return val


@lru_cache()
def get_user_config_filename():
    config_key = "ci_config" if get("is_ci") else "user_config"
    return get(config_key)


class CustomConfigParser(configparser.ConfigParser):
    def __getitem__(self, key):
        if key != self.default_section and not self.has_section(key):
            self.add_section(key)
        return super().__getitem__(key)


class MultiConfigParser(CustomConfigParser):
    def _write_section(self, fp, section_name, section_items, delimiter):
        """Write a single section to the specified `fp'. Extended to write multi-value, single key."""
        fp.write("[{}]\n".format(section_name))
        for key, value in section_items:
            value = self._interpolation.before_write(self, section_name, key, value)
            if isinstance(value, list):
                values = value
            else:
                values = [value]
            for value in values:
                if self._allow_no_value and value is None:
                    value = ""
                else:
                    value = delimiter + str(value).replace("\n", "\n\t")
                fp.write("{}{}\n".format(key, value))
        fp.write("\n")

    def _join_multiline_values(self):
        """Handles newlines being parsed as bogus values."""
        defaults = self.default_section, self._defaults
        all_sections = itertools.chain((defaults,), self._sections.items())
        for section, options in all_sections:
            for name, val in options.items():
                if isinstance(val, list):
                    # check if this is a multi value
                    length = len(val)
                    if length > 1:
                        last_entry = val[length - 1]
                        # if the last entry is empty (newline!), clear it out
                        if not last_entry:
                            del val[-1]
                    # restore it back to single value
                    if len(val) == 1:
                        val = val[0]
                val = self._interpolation.before_read(self, section, name, val)
                options.force_set(name, val)


class CustomInterpolation(configparser.BasicInterpolation):
    def before_get(
        self,
        parser,
        section: configparser._SectionName,
        option: str,
        value: str,
        defaults: configparser._Section,
    ) -> str:
        val = super().before_get(parser, section, option, value, defaults)
        if get("is_ci"):
            return os.getenv(val) or ""
        return val


def init_user_config():
    global user_config
    user_config = CustomConfigParser(interpolation=CustomInterpolation())
    user_config.read(get_user_config_filename())


def get_user_config():
    if user_config is None:
        init_user_config()
    return user_config


def get_user(section, key, default=None):
    return get_user_config().get(section, key, fallback=default)


def shutdown():
    if not get("is_ci") and user_config is not None:
        user_filename = get_user_config_filename()
        attributes = 0
        restore_hidden = False
        if os.name == "nt" and os.path.exists(user_filename):
            import win32api
            import win32con

            attributes = win32api.GetFileAttributes(user_filename)
            restore_hidden = attributes & win32con.FILE_ATTRIBUTE_HIDDEN
            win32api.SetFileAttributes(
                user_filename, attributes & ~win32con.FILE_ATTRIBUTE_HIDDEN
            )
        with open(user_filename, "w") as user_config_file:
            user_config.write(user_config_file)
        if restore_hidden:
            win32api.SetFileAttributes(user_filename, attributes)


def generate_config(config_path, parser_func):
    # Generalized config generator. parser_func is responsible with returning a valid config object
    global config
    global config_filepath

    if config_path is not None and os.path.isfile(config_path):
        tree = parse(config_path)
        if tree is None:
            return False
        root = tree.getroot()
        if root is None:
            return False

        # Read config xml
        try:
            config = parser_func(root)
            config_filepath = config_path
        except Exception as e:
            print(f"Config exception: {e}")
            return False

        # Add CI information
        config["is_ci"] = (
            os.getenv("CLIQUESYNC_CI") is not None or os.getenv("CI") is not None
        )
        config["checksum_file"] = ".checksum"

        return True

    return False
