# Linux Repository Mirror System

A containerized Linux repository mirroring system that synchronizes APT and DNF/YUM repositories for multiple Linux distributions.

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

The `status --verify` command performs local repository structure integrity checking to ensure your mirrored repositories are complete and functional.

### What it checks:

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

### What it does NOT check:

- **No cryptographic verification**: Does not verify GPG signatures or checksums against upstream
- **No content validation**: Does not compare file hashes with upstream repositories
- **No network verification**: Does not fetch remote metadata for comparison
- **No package integrity**: Does not verify individual package checksums

The verification is a **local filesystem sanity check** to ensure sync operations created usable repository structures, not cryptographic authenticity verification (which is handled by package managers when using the repositories).

Example output:
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
```

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
    └── rhel/
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