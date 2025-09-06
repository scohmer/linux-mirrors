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

**Enable rootless containers (recommended):**
```bash
sudo sysctl kernel.unprivileged_userns_clone=1
echo 'kernel.unprivileged_userns_clone=1' | sudo tee -a /etc/sysctl.conf
```

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
├── tests/              # Test suite
├── docs/               # Documentation
├── main.py             # Entry point
├── requirements.txt    # Dependencies
└── setup.py           # Package configuration
```

### Running Tests

```bash
python -m pytest tests/
```

### Building

```bash
python setup.py build
python setup.py sdist bdist_wheel
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