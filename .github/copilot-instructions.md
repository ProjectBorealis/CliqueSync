# CliqueSync Copilot Instructions

## Project Overview
CliqueSync is a simple but featureful workspace synchronization tool for Unreal Engine projects, especially those using Git! It automates complex workflows including git operations, engine version management, binary distribution, and build processes.

## Architecture

### Core Modules (`pbpy/`)
- **pbconfig.py**: XML-based configuration system with user/CI config overlays (`.user-sync` / `.ci-sync`)
- **pbgit.py**: Git operations, LFS management, credential handling, version checking
- **pbunreal.py**: Unreal Engine integration, project files, version management, building
- **pbgh.py**: GitHub/binary distribution management
- **pbtools.py**: Common utilities, process execution, error state management
- **pblog.py**: Centralized logging with color support

### Entry Points
- **pbsync/__main__.py**: CLI interface with argument parsing and command routing
- **build.bat/build.sh**: PyInstaller build scripts for distribution

## Key Patterns

### Error State Management
Use `pbtools.error_state()` for fatal errors. Check `pbtools.check_error_state()` before operations.
```python
if not condition:
    error_state("Description of error", fatal_error=True)
```

### Configuration Access
```python
# Main config (CliqueSync.xml)
value = pbconfig.get("config_key")
# User config (.user-sync or .ci-sync)
value = pbconfig.get_user("section", "key", "default")
```

### Cached Git Operations
Use `@lru_cache()` for expensive git operations:
```python
@lru_cache()
def get_current_branch_name():
    return pbtools.get_one_line_output([get_git_executable(), "branch", "--show-current"])
```

### Version Management
- Engine versions: `pbunreal.get_engine_version_with_prefix()` returns `uev:` prefixed versions
- Project versions: stored in `Config/DefaultGame.ini` as `ProjectVersion=`
- Binary versioning: tied to git tags/commits for distribution

## Development Workflow

### Build Process
1. **Dependencies**: `pipenv install` → `dependencies.bat/sh`
2. **PyInstaller Setup**: `install_pyinstaller.bat` (builds from source to avoid false positives)
3. **Build**: `build.bat/sh` → generates `dist/CliqueSync.exe`

### Configuration Structure
Main config (`CliqueSync.xml`) defines:
- Git versions (git, git-lfs, gcm)
- Engine settings (versionator config)
- Branch expectations and URLs
- Publishing/deployment settings

### Sync Operations
The `--sync` command orchestrates:
1. Git tool version validation/auto-update
2. Remote connectivity checks
3. Branch synchronization and conflict resolution
4. Binary/engine version management
5. Unreal project file generation

## Platform Considerations
- **Windows**: Full UAC integration, registry management for engine associations
- **Linux**: Direct Python execution supported, simplified tool management
- **Dependencies**: Platform-specific packages in `Pipfile` (pywin32, pefile for Windows)

## Integration Points
- **Git ecosystem**: Extensive integration with Git, Git LFS, Git Credential Manager
- **Unreal Engine**: Project file manipulation, engine registration, build automation
- **Distribution**: GitHub releases, Steam (SteamCMD), Itch.io (Butler), custom dispatch
- **External tools**: P4Merge, ReSharper, Visual Studio integration

## Common Tasks
- Add new sync operations in `sync_handler()` function
- Extend build hooks in `build_hooks` dictionary
- Add publishers in `PUBLISHERS` dictionary
- Configuration keys follow XML path notation in `pbsync_config_parser_func()`