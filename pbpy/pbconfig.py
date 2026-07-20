import configparser
import itertools
import os
from pathlib import Path
from xml.etree.ElementTree import parse

from pbpy import pblog, pbtools

# Singleton Config and path to said config
config: dict[str, str | list[str] | list[dict[str, str]] | bool] | None = None
config_filepath = None

user_config : MergedConfigParser | None = None
global_user_config : configparser.ConfigParser | None = None
project_user_config : configparser.ConfigParser | None = None


def validated_get(key):
    if key is None or config is None:
        pbtools.error_state(f"Invalid config get request: {key}", hush=True)
        return None, False
    val = config.get(str(key))
    # this should be checked by missing_keys in pbsync_config_parser_func, but checking here just in case
    # TODO: remove?
    if val is None:
        pbtools.error_state(f"{key} is not set in config", hush=True)
        return None, False
    # We checked None values that distingiush something is a required key and must be set at startup
    # now, we can check for empty values which we can translate into a None to represent an unset value
    # this allows more flexibility in type handling for None values rather empty strings
    success = True
    if isinstance(val, str):
        # strip to allow for xml formatting
        val = val.strip()
        if val == "":
            pblog.warning(f"{key} is not set in config")
            success = False
            # TODO: replace with None for type handling
            # val = None
    elif isinstance(val, list):
        if len(val) < 1:
            pblog.warning(f"{key} is not set in config")
            success = False
            # TODO: should we replace empty lists with None?
            # val = None
        else:
            success = False
            for idx in range(len(val)):
                item = val[idx]
                if isinstance(item, str):
                    # strip to allow for xml formatting
                    item = item.strip()
                    val[idx] = item
                if not val[idx]:
                    pblog.warning(f"{key}[{idx}] is not set in config")
                    # TODO: replace with None for type handling
                    if not isinstance(item, str):
                        val[idx] = {}
                else:
                    success = True
    return val, success


def get(key):
    val, _ = validated_get(key)
    return val


def get_global_user_config_filename():
    config_key = "ci_config" if get("is_ci") else "user_config"
    return get(config_key)


def get_project_user_config_filename():
    try:
        # prevent circular imports
        from pbpy import pbunreal

        uproject_name = pbunreal.get_uproject_name()
        if uproject_name:
            uproject_path = pbunreal.get_uproject_path()
            project_dir = uproject_path.parent

            global_file = get_global_user_config_filename()
            basename = Path(global_file).name
            project_config_path = project_dir / basename
            resolved_project_config_path = project_config_path.resolve()
            global_path = Path(global_file).resolve()
            if resolved_project_config_path != global_path:
                if resolved_project_config_path.exists():
                    return str(resolved_project_config_path)
    except Exception:
        pass
    return None


def get_user_config_filename():
    project_file = get_project_user_config_filename()
    if project_file:
        return project_file
    return get_global_user_config_filename()


class CustomConfigParser(configparser.ConfigParser):
    def __getitem__(self, key):
        if key != self.default_section and not self.has_section(key):
            self.add_section(key)
        return super().__getitem__(key)


class MergedConfigParser(CustomConfigParser):
    def __init__(self, global_parser, project_parser, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.global_parser = global_parser
        self.project_parser = project_parser
        self._update_from_parsers()

    def _update_from_parsers(self):
        self.clear()
        for section in self.global_parser.sections():
            if not self.has_section(section):
                self.add_section(section)
            for option, value in self.global_parser.items(section):
                self[section][option] = value
        if self.project_parser:
            for section in self.project_parser.sections():
                if not self.has_section(section):
                    self.add_section(section)
                for option, value in self.project_parser.items(section):
                    self[section][option] = value


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
        if get("is_ci"):
            return os.getenv(value) or ""
        return value

    def before_set(self, parser, section, option, value):
        return value


def init_user_config():
    global user_config, global_user_config, project_user_config

    global_user_config = CustomConfigParser(interpolation=CustomInterpolation())
    global_file = get_global_user_config_filename()
    if global_file and os.path.exists(global_file):
        global_user_config.read(global_file)

    project_file = get_project_user_config_filename()
    if project_file:
        project_user_config = CustomConfigParser(interpolation=CustomInterpolation())
        project_user_config.read(project_file)
        pblog.info(
            f"Loading project-specific user config: {project_file}. This config will overlay on top of the global user config: {global_file}"
        )
    else:
        project_user_config = None

    user_config = MergedConfigParser(
        global_user_config, project_user_config, interpolation=CustomInterpolation()
    )


def get_user_config():
    if user_config is None:
        init_user_config()
    return user_config


def get_user(section, key, default=None):
    return get_user_config().get(section, key, fallback=default)


def write_config_file(filename, parser):
    if parser is None:
        return
    attributes = 0
    restore_hidden = False
    if os.name == "nt" and os.path.exists(filename):
        import win32api
        import win32con

        attributes = win32api.GetFileAttributes(filename)
        restore_hidden = attributes & win32con.FILE_ATTRIBUTE_HIDDEN
        win32api.SetFileAttributes(
            filename, attributes & ~win32con.FILE_ATTRIBUTE_HIDDEN
        )

    parent_dir = os.path.dirname(filename)
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)

    with open(filename, "w") as config_file:
        parser.write(config_file)

    if restore_hidden:
        win32api.SetFileAttributes(filename, attributes)


def shutdown():
    if not get("is_ci") and user_config is not None:
        global global_user_config, project_user_config
        # Propagate changes from merged user_config back to underlying parsers
        for section in user_config.sections():
            for option, value in user_config.items(section):
                if project_user_config is not None:
                    if project_user_config.has_option(
                        section, option
                    ) or not global_user_config.has_option(section, option):
                        if not project_user_config.has_section(section):
                            project_user_config.add_section(section)
                        project_user_config[section][option] = value
                        continue

                if not global_user_config.has_section(section):
                    global_user_config.add_section(section)
                global_user_config[section][option] = value

        global_file = get_global_user_config_filename()
        if global_file:
            write_config_file(global_file, global_user_config)

        project_file = get_project_user_config_filename()
        if project_file:
            write_config_file(project_file, project_user_config)


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
