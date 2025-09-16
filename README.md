# Linux Repository Mirror System

A containerized Linux repository mirroring system that synchronizes APT and DNF/YUM repositories for multiple Linux distributions.

## Version 1.02

### What's New
- **Enhanced RHEL Repository Support**: Streamlined RHEL component configuration with focus on essential repositories (BaseOS, AppStream, codeready-builder, supplementary)
- **Improved APT Repository Sync**: Better handling of additional repositories (security, updates, backports) for Debian and Ubuntu distributions
- **Enhanced URL Normalization**: Improved URL handling and normalization across all sync engines
- **Modular APT Sync Engine**: Refactored APT sync engine with separate methods for Debian and Ubuntu additional repositories
- **Better Architecture Support**: Enhanced support for ARM architectures in Ubuntu repositories using ports.ubuntu.com

## Features

- **Multi-Distribution Support**: Debian, Ubuntu, Rocky Linux, RHEL, and Kali Linux
- **Containerized Sync**: Isolated environments for each distribution and version
- **Interactive TUI**: NCurses-based interface for easy repository selection
- **Debug Interface**: Comprehensive troubleshooting and monitoring tools
- **Systemd Integration**: Automated scheduling with service units and timers
- **Storage Management**: Efficient handling of large repository data
- **Centralized Configuration**: YAML-based configuration management

## Supported Distributions

- **Debian** (version 7+)
- **Ubuntu** (version 18.04+)
- **Rocky Linux** (version 8+)
- **Red Hat Enterprise Linux** (version 8+)
- **EPEL** (Extra Packages for Enterprise Linux - versions 8, 9, 10)
- **Kali Linux**

## Installation

### Prerequisites

- Python 3.8+
- Podman (recommended) or Docker
- Linux system (tested on Ubuntu/Debian/RHEL/Rocky)

#### Installing Podman

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install -y podman
```

**RHEL/Rocky/CentOS:**
```bash
sudo dnf install -y podman
```

**Enable rootless containers (distribution-specific):**

**Debian/Ubuntu:**
```bash
sudo sysctl kernel.unprivileged_userns_clone=1
echo 'kernel.unprivileged_userns_clone=1' | sudo tee -a /etc/sysctl.conf
```

**RHEL/Rocky/CentOS (if user namespaces are disabled):**
```bash
# Check if user namespaces are enabled (should be > 0)
cat /proc/sys/user/max_user_namespaces

# If disabled (0), enable with:
echo 'user.max_user_namespaces=28633' | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

**Note:** Most modern distributions enable user namespaces by default. If rootless containers work without configuration, no additional setup is required.

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Install the Package

```bash
pip install -e .
```

## Usage

### Interactive TUI Mode

Launch the main interface:

```bash
linux-mirrors
```

### Command Line Usage

Sync all enabled distributions:

```bash
linux-mirrors sync --all
```

Sync specific distribution and version:

```bash
linux-mirrors sync --distribution debian --version bookworm
```

Setup systemd services:

```bash
# System-wide services (requires root)
sudo linux-mirrors setup-systemd

# User-level services
linux-mirrors setup-systemd --user
```

Check system status:

```bash
linux-mirrors status
```

Verify repository integrity:

```bash
linux-mirrors status --verify
```

Verify file integrity with GPG signatures and checksums:

```bash
linux-mirrors status --file-integrity
```

Launch debug interface:

```bash
linux-mirrors debug
```

Storage management:

```bash
# Show storage information
linux-mirrors storage --info

# Clean up old sync data
linux-mirrors storage --cleanup
```

## Repository Verification

The system provides two levels of repository verification to ensure your mirrored repositories are complete, functional, and secure.

### Basic Verification (`--verify`)

The `status --verify` command performs local repository structure integrity checking to ensure your mirrored repositories are complete and functional.

**What it checks:**

**APT Repositories (Debian, Ubuntu, Kali):**
- Directory structure (`dists/{version}` directories)
- Release files with proper suite/codename information
- Packages files for each component and architecture
- Package pool directories containing `.deb` files
- Architecture availability based on distribution version

**YUM Repositories (Rocky Linux, RHEL):**
- Repository structure for each architecture
- `repodata` directories and `repomd.xml` metadata files
- RPM packages in expected locations
- Valid XML structure in metadata files

This is a **local filesystem sanity check** to ensure sync operations created usable repository structures.

### File Integrity Verification (`--file-integrity`)

The `status --file-integrity` command performs comprehensive cryptographic verification of repository authenticity and integrity.

**What it checks:**

**APT Repositories (Debian, Ubuntu, Kali):**
- **GPG Signature Verification**: Validates Release/InRelease files using system GPG keyring
- **SHA256 Checksum Verification**: Verifies all metadata files against checksums in Release file
- **Signature File Detection**: Checks for both inline (InRelease) and detached (Release.gpg) signatures
- **Complete Metadata Validation**: Ensures all Packages files match their expected checksums

**YUM Repositories (Rocky Linux, RHEL):**
- **GPG Signature Verification**: Validates repomd.xml files using detached signatures (repomd.xml.asc)
- **SHA256 Checksum Verification**: Verifies repository metadata files against checksums in repomd.xml
- **Multi-Architecture Support**: Checks signatures and checksums for all configured architectures
- **Repository Consistency**: Ensures metadata integrity across BaseOS and AppStream components

**Prerequisites for File Integrity Verification:**
- System must have `gpg` command available
- Repository GPG keys must be imported to system keyring
- Sufficient disk I/O performance (verification can take several minutes for large repositories)

**What it does NOT check:**
- Individual package (.deb/.rpm) file signatures (handled by package managers)
- Network connectivity or upstream comparison
- Package content or binary integrity

### Example Output

**Basic verification:**
```bash
$ linux-mirrors status --verify
=== Repository Verification ===
Repository verification: 12 total, 10 verified, 1 missing, 1 failed

Verified repositories:
  ✓ debian bookworm: File integrity verified against origin repository
  ✓ ubuntu jammy: File integrity verified against origin repository

Issues found:
  ? debian bullseye: Repository directory not found
  ✗ rocky 8: Missing repomd.xml for x86_64
```

**File integrity verification:**
```bash
$ linux-mirrors status --file-integrity
=== Repository File Integrity Verification ===
Checking GPG signatures and SHA256 checksums... (this may take several minutes)

File integrity verification: 5 total repositories
  ✓ 4 verified, ✗ 1 failed, ? 0 missing
  GPG signatures verified: 4/5 repositories
  SHA256 checksums verified: 247/250 files

Verified repositories:
  ✓ debian bookworm: ✓ GPG, 45/45 checksums
  ✓ ubuntu jammy: ✓ GPG, 38/38 checksums
  ✓ rocky 9: ✓ GPG, 164/164 checksums

Issues found:
  ✗ ubuntu mantic: GPG verification failed for ubuntu mantic InRelease: gpg: Can't check signature: No public key (✗ GPG, 0/0 checksums)
```

## Configuration

The system uses YAML configuration files located at:
- System: `/etc/linux-mirrors/config.yaml`
- User: `~/.config/linux-mirrors/config.yaml`

### Example Configuration

```yaml
base_path: /srv/mirror
apt_path: /srv/mirror/apt
yum_path: /srv/mirror/yum
container_runtime: podman
max_concurrent_syncs: 3
log_level: INFO

distributions:
  debian:
    name: debian
    type: apt
    versions:
      - bullseye
      - bookworm
      - trixie
    mirror_urls:
      - http://deb.debian.org/debian/
    components:
      - main
      - contrib
      - non-free
    architectures:
      - amd64
      - arm64
    enabled: true
    sync_schedule: daily

  epel:
    name: epel
    type: yum
    versions:
      - "8"
      - "9"
      - "10"
    mirror_urls:
      - https://dl.fedoraproject.org/pub/epel
    components:
      - Everything
    architectures:
      - x86_64
      - aarch64
    enabled: true
    include_gpg_keys: true
    gpg_key_urls:
      - https://dl.fedoraproject.org/pub/epel/RPM-GPG-KEY-EPEL-8
      - https://dl.fedoraproject.org/pub/epel/RPM-GPG-KEY-EPEL-9
      - https://dl.fedoraproject.org/pub/epel/RPM-GPG-KEY-EPEL-10
```

## EPEL Support

**EPEL (Extra Packages for Enterprise Linux)** is a special interest group from the Fedora Project that provides additional packages for RHEL and compatible distributions like Rocky Linux and AlmaLinux.

### Features

- **Automatic GPG Key Management**: GPG keys for signature verification are automatically downloaded and configured
- **Multiple Architecture Support**: x86_64, aarch64, ppc64le, s390x (depending on EPEL version)
- **Version Support**: EPEL 8, 9, and 10 (EPEL 7 is archived but accessible)
- **Integrated with YUM Sync Engine**: Uses the same containerized sync process as Rocky Linux and RHEL

### EPEL Versions

- **EPEL 8**: Compatible with RHEL 8, Rocky Linux 8, AlmaLinux 8
- **EPEL 9**: Compatible with RHEL 9, Rocky Linux 9, AlmaLinux 9
- **EPEL 10**: Compatible with RHEL 10, Rocky Linux 10, AlmaLinux 10

### Repository Structure

EPEL repositories are organized differently from standard RHEL repositories:
- Uses `Everything` as the primary component instead of `BaseOS`/`AppStream`
- Repository path: `{version}/Everything/{arch}/`
- No ISO images (packages only)

### Configuration Notes

- GPG signature verification is enabled by default for security
- GPG keys are automatically downloaded from https://dl.fedoraproject.org/pub/epel/
- Mirror URL uses the official Fedora download infrastructure
- Supports both x86_64 and ARM64 architectures in the default configuration

## Architecture

### Directory Structure

```
/srv/mirror/
├── apt/
│   ├── debian/
│   ├── ubuntu/
│   └── kali/
└── yum/
    ├── rocky/
    ├── rhel/
    └── epel/
```

### Components

1. **Configuration Management** (`src/config/`): Centralized YAML-based configuration
2. **Container Orchestration** (`src/containers/`): Podman integration for isolated syncing
3. **Repository Sync Engines** (`src/sync/`): APT and YUM synchronization logic
4. **TUI Interfaces** (`src/tui/`): Main interface and debugging tools
5. **Systemd Integration** (`src/systemd/`): Service unit generation and management
6. **Storage Management** (`src/storage/`): Directory structure and cleanup utilities

## Development

### Project Structure

```
linux-mirrors/
├── src/
│   ├── config/          # Configuration management
│   ├── containers/      # Podman orchestration
│   ├── sync/           # Repository sync engines
│   ├── tui/            # User interfaces
│   ├── systemd/        # Service generation
│   └── storage/        # Storage management
├── tests/              # Comprehensive test suite
│   ├── README.md       # Testing documentation
│   ├── conftest.py     # Test configuration and fixtures
│   ├── test_*.py       # Unit tests for each component
│   ├── test_integration.py  # Integration tests
│   └── test_end_to_end.py   # End-to-end workflow tests
├── requirements.txt    # Runtime dependencies
├── requirements-test.txt    # Testing dependencies
├── pytest.ini         # Test configuration
├── Makefile           # Development commands
├── TESTING_SUMMARY.md # Test coverage and results
└── setup.py           # Package configuration
```

### Testing

The project includes a comprehensive test suite with 73+ test cases covering all major components:

#### Quick Start
```bash
# Install test dependencies
pip install -r requirements-test.txt

# Run all tests
pytest

# Run with coverage report
pytest --cov=src --cov-report=html
```

#### Test Categories
```bash
# Unit tests (fast, isolated)
make test-unit

# Integration tests
make test-integration

# End-to-end tests
make test-e2e

# Skip slow tests
make test-fast

# Parallel execution
make test-parallel
```

#### Coverage Results
- **ConfigManager**: 97% coverage
- **SyncEngines**: 96% coverage  
- **StorageManager**: 83% coverage

See [tests/README.md](tests/README.md) for detailed testing documentation.

### Building

```bash
make build          # Clean build
make clean          # Remove build artifacts
```

### Code Quality

```bash
make lint           # Run code linting
make type-check     # Run type checking
make format         # Format code
make quality-check  # Run all quality checks
```

## Troubleshooting

### Debug Interface

The debug interface provides comprehensive troubleshooting tools:

- Container status and log viewing
- Storage information and cleanup
- System resource monitoring
- Configuration validation

Access via:

```bash
linux-mirrors debug
```

### Common Issues

1. **Permission Errors**: Run with appropriate privileges or use `--user` mode
2. **Container Runtime**: Ensure Podman is installed and accessible (`podman --version`)
3. **Rootless Containers**: For user-mode operation, ensure rootless containers are configured
4. **Disk Space**: Check available space with `linux-mirrors storage --info`
5. **Container Failures**: View logs through the debug interface

### Logging

Logs are written to:
- System: `/var/log/linux-mirrors.log`
- User: `~/.local/log/linux-mirrors.log`

Set log level with `--log-level DEBUG` for detailed output.

## License

MIT License - see LICENSE file for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Submit a pull request

## Support

- GitHub Issues: Report bugs and request features
- Documentation: See `docs/` directory
- Debug Interface: Built-in troubleshooting tools