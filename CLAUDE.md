# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Linux repository mirroring system that synchronizes APT and DNF/YUM repositories for multiple Linux distributions. The project uses containerization to create isolated environments for each distribution and version.

### Supported Distributions
- Debian (version 7+)
- Ubuntu (version 18.04+) 
- Rocky Linux (version 8+)
- Red Hat Enterprise Linux (version 8+)
- Kali Linux

## Project Architecture

The system is designed around the following key architectural principles:

### Directory Structure
- Base directory: `/srv/mirror`
- APT repositories: `/srv/mirror/apt/`
- YUM repositories: `/srv/mirror/yum/`
- Distribution subdirectories: `debian/`, `ubuntu/`, `kali/`, `rocky/`, `rhel/`

### Containerization Strategy
- Each distribution and version combination gets its own dedicated container
- Containers are used to isolate the synchronization process for different distributions
- Centralized configuration management to reduce repetition across containers

### Service Management
- Systemd service units and timers are created for each distribution
- Automated scheduling of repository synchronization tasks

### User Interface
- NCurses TUI menu system for interactive repository selection
- Options to sync all repositories or select specific distributions/versions
- A Debugging TUI menu system for troubleshooting problems with the framework

## Development Status

This project is fully implemented with comprehensive functionality:

### ✅ Completed Components
- **Configuration Management**: YAML-based configuration with defaults and validation
- **Container Orchestration**: Podman/Docker integration for isolated syncing
- **Repository Sync Engines**: APT (apt-mirror/debmirror) and YUM (reposync) support
- **TUI Interfaces**: Main interactive interface and debugging tools
- **Systemd Integration**: Service unit and timer generation for automation
- **Storage Management**: Directory structure, cleanup, and space monitoring
- **Command Line Interface**: Full CLI with all major operations

### ✅ Testing & Quality
- **73+ Test Cases**: Comprehensive unit, integration, and end-to-end tests
- **High Coverage**: 97% ConfigManager, 96% SyncEngines, 83% StorageManager
- **Test Documentation**: Detailed testing guide and examples
- **Development Tools**: Makefile, pytest configuration, code quality tools

### ✅ Documentation
- **User Documentation**: Complete README with usage examples
- **Developer Documentation**: Architecture overview and development guide  
- **Test Documentation**: Comprehensive testing guide and coverage reports
- **Configuration Examples**: Sample configurations for common scenarios

### 🔧 Current Capabilities
The system can now:
- Sync multiple Linux distributions (Debian, Ubuntu, Kali, Rocky, RHEL)
- Run in interactive TUI mode or via command line
- Generate systemd services for automated syncing
- Manage storage and cleanup old sync data
- Provide debugging and monitoring tools
- Handle both user and system-wide deployments

## Key Implementation Areas

When implementing this system, focus on these core components:

1. **Configuration Management**: Centralized config system to manage all distributions and versions
2. **Container Orchestration**: Docker/Podman setup for isolated sync environments  
3. **Repository Sync Logic**: APT (apt-mirror, debmirror) and YUM (reposync) integration
4. **Systemd Integration**: Service units and timer configuration for automated syncing
5. **TUI Interface**: NCurses-based menu for user interaction and repository selection
6. **Storage Management**: Efficient handling of large repository data in `/srv/mirror`

## Project Goals

- Full repository synchronization for specified Linux distributions
- Containerized, isolated sync processes
- Automated scheduling via systemd
- User-friendly selection interface
- Centralized configuration management