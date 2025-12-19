# CliqueSync

Simple but featureful workspace synchronization tool for Unreal Engine projects, especially those using Git!

## Development

### Pipenv

Before running any scripts, use `pipenv install` and then `pipenv shell`.

### Dependencies

Run `dependencies.bat` on Windows or `dependencies.sh` on Linux.

### Setup

On Linux, this step can be skipped.

PyInstaller is required for executable generation, and it should be built from source to prevent false positive virus detections of generated CliqueSync executable.

You can run `install_pyinstaller.bat` to do this automatically.

### Build

#### Windows

To generate a binary file from python source code, just run `build.bat` script. If generation was successful, the binary file will be put inside `dist` folder. To start using, generated executable should be put into root folder of the Unreal Engine project.

It's recommended that you have UPX on PATH to compress the built binary.

#### Linux

On Linux systems, run the `build.sh` script to generate binary file.

But, since most Linux systems come with a version of Python already available, another option is to run it directly:

```
git clone https://github.com/ProjectBorealis/CliqueSync

PYTHONPATH=<path-to-local-CliqueSync> python <path-to-local-CliqueSync>/pbsync/pbsync.py --help
```

### Contribution

Everyone is welcomed to fork the repository, or open pull requests and new issues in this main repository.

## Usage

### Sample usage

You can refer to our [PBCore repo](https://github.com/ProjectBorealis/PBCore) for an example of usage.

Essentially, we use a batch script to sync CliqueSync with the remote branch, and then launch CliqueSync (`UpdateProject.bat`). We have our configuration file in `CliqueSync.xml`.

`CliqueSync.exe` and `ueversionator.exe` are distributed as part of the repo, at the root game project level.

`.ueversionator` in the repo configures the engine download.

Additional scripts for managing various CliqueSync functionality can be found in the `Script` folder.

### Available Commands

List of available commands can be printed to console by passing `--help` to generated executable.
