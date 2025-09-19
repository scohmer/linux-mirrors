#!/usr/bin/env python3

import os
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from pathlib import Path
from config.manager import DistributionConfig
from containers.orchestrator import ContainerOrchestrator

logger = logging.getLogger(__name__)

class SyncEngine(ABC):
    def __init__(self, dist_config: DistributionConfig, orchestrator: ContainerOrchestrator):
        self.dist_config = dist_config
        self.orchestrator = orchestrator
        self.container_id: Optional[str] = None
    
    @abstractmethod
    def generate_sync_command(self, version: str) -> List[str]:
        pass
    
    @abstractmethod
    def validate_config(self) -> bool:
        pass
    
    async def sync_version(self, version: str) -> Dict[str, Any]:
        try:
            # Validate configuration
            if not self.validate_config():
                raise ValueError("Invalid configuration for sync")
            
            # Generate sync command
            sync_command = self.generate_sync_command(version)
            
            # Create and start container
            self.container_id = self.orchestrator.create_sync_container(
                self.dist_config.name,
                version,
                sync_command
            )
            
            self.orchestrator.start_sync_container(self.container_id)
            
            # Monitor sync progress
            result = await self._monitor_sync()
            
            return {
                'distribution': self.dist_config.name,
                'version': version,
                'status': result['status'],
                'container_id': self.container_id,
                'logs': result.get('logs', '')
            }
            
        except Exception as e:
            logger.error(f"Sync failed for {self.dist_config.name} {version}: {e}")
            return {
                'distribution': self.dist_config.name,
                'version': version,
                'status': 'failed',
                'error': str(e),
                'container_id': self.container_id
            }
    
    async def _monitor_sync(self) -> Dict[str, Any]:
        if not self.container_id:
            raise ValueError("No container ID available for monitoring")
        
        while True:
            status = self.orchestrator.get_container_status(self.container_id)
            
            # Handle container not found (removed externally)
            if status.get('status') == 'not found' or 'error' in status:
                if 'not found' in str(status.get('error', '')).lower():
                    logger.warning(f"Container {self.container_id} was removed externally")
                    return {
                        'status': 'failed',
                        'logs': 'Container was removed before sync completed'
                    }
                else:
                    logger.error(f"Error monitoring container {self.container_id}: {status.get('error')}")
                    return {
                        'status': 'failed',
                        'logs': f"Monitoring error: {status.get('error', 'Unknown error')}"
                    }
            
            if status.get('status') == 'exited':
                # Container finished
                logs = self.orchestrator.get_container_logs(self.container_id)
                return {
                    'status': 'completed',
                    'logs': logs
                }
            elif status.get('status') in ['dead', 'oom', 'killed']:
                logs = self.orchestrator.get_container_logs(self.container_id)
                return {
                    'status': 'failed',
                    'logs': logs
                }
            
            # Wait before checking again
            await asyncio.sleep(30)

class AptSyncEngine(SyncEngine):
    def generate_sync_command(self, version: str) -> List[str]:
        mirror_config_path = "/mirror/apt-mirror.list"
        
        # Create apt-mirror configuration
        config_content = self._generate_apt_mirror_config(version)
        
        return [
            'sh', '-c', f'''
            echo "{config_content}" > {mirror_config_path} &&
            apt-mirror {mirror_config_path}
            '''
        ]
    
    def _get_version_specific_urls(self, version: str) -> List[str]:
        """Get appropriate mirror URLs based on version for distribution-specific handling"""
        # Special handling for Debian archived versions
        if self.dist_config.name == "debian":
            # Ensure we're using codenames for Debian
            version = self._get_debian_codename(version)
            archived_versions = ["wheezy", "jessie", "stretch", "buster"]
            if version in archived_versions:
                return ["http://archive.debian.org/debian"]
            else:
                # Use current Debian mirrors for bullseye, bookworm, trixie, etc.
                # Strip trailing slashes from all configured URLs to prevent double slashes
                return [url.rstrip('/') for url in self.dist_config.mirror_urls]
        
        # Special handling for Ubuntu EOL versions
        elif self.dist_config.name == "ubuntu":
            # Ubuntu versions that have reached EOL and moved to old-releases
            eol_versions = ["mantic"]  # Ubuntu 23.10 - EOL July 11, 2024
            if version in eol_versions:
                return ["http://old-releases.ubuntu.com/ubuntu"]
            else:
                # Use current Ubuntu mirrors for supported versions
                return [url.rstrip('/') for url in self.dist_config.mirror_urls]
        
        # For other distributions, strip trailing slashes from configured mirror URLs
        return [url.rstrip('/') for url in self.dist_config.mirror_urls]

    def _get_debian_codename(self, version: str) -> str:
        """Map Debian numeric versions to codenames"""
        debian_version_map = {
            '7': 'wheezy',
            '8': 'jessie',
            '9': 'stretch',
            '10': 'buster',
            '11': 'bullseye',
            '12': 'bookworm',
            '13': 'trixie',
            '14': 'forky',
            'sid': 'sid',
            'unstable': 'unstable'
        }
        # Return mapped codename if numeric version provided, otherwise return as-is
        return debian_version_map.get(version, version)

    def _filter_components_for_version(self, version: str, components: list) -> list:
        """Filter out components that don't exist for specific Debian versions"""
        if self.dist_config.name != "debian":
            return components

        # Map to codename if needed
        codename = self._get_debian_codename(version)

        # non-free-firmware was introduced in Debian 12 (bookworm)
        # Prior versions don't have this component
        versions_without_nonfree_firmware = [
            "wheezy", "jessie", "stretch", "buster", "bullseye"
        ]

        if codename in versions_without_nonfree_firmware:
            filtered_components = [c for c in components if c != "non-free-firmware"]
            if "non-free-firmware" in components:
                logger.info(f"Excluding 'non-free-firmware' component for Debian {codename} (not available)")
            return filtered_components

        return components

    def _generate_apt_mirror_config(self, version: str) -> str:
        # Map numeric Debian versions to codenames if needed
        if self.dist_config.name == "debian":
            version = self._get_debian_codename(version)
            logger.debug(f"Using Debian codename: {version}")

        base_path = "/mirror"
        config_lines = [
            f"set base_path {base_path}",
            f"set mirror_path {base_path}/mirror",
            f"set skel_path {base_path}/skel",
            f"set var_path {base_path}/var",
            f"set cleanscript {base_path}/var/clean.sh",
            f"set defaultarch {' '.join(self.dist_config.architectures)}",
            f"set postmirror_script {base_path}/var/postmirror.sh",
            f"set run_postmirror 0",
            f"set nthreads 20",
            f"set _tilde 0",
            ""
        ]

        # Get version-specific mirror URLs
        mirror_urls = self._get_version_specific_urls(version)

        # Add main repository lines
        # Filter components based on version availability
        filtered_components = self._filter_components_for_version(version, self.dist_config.components)
        components = " ".join(filtered_components)
        for mirror_url in mirror_urls:
            # URLs already have trailing slashes removed in _get_version_specific_urls
            normalized_url = mirror_url
            
            # Generate architecture-specific deb lines for apt-mirror
            for arch in self.dist_config.architectures:
                # Skip 'all' architecture for wheezy due to repository availability issues
                if version == "wheezy" and arch == "all":
                    logger.warning(f"Skipping 'all' architecture for {version} - not reliably available")
                    continue
                
                # For Ubuntu, ARM architectures (arm64, armhf) use ports.ubuntu.com instead of archive.ubuntu.com
                if (self.dist_config.name == "ubuntu" and arch in ["arm64", "armhf"] and 
                    "archive.ubuntu.com" in normalized_url):
                    arch_specific_url = normalized_url.replace("archive.ubuntu.com/ubuntu", "ports.ubuntu.com/ubuntu-ports")
                else:
                    arch_specific_url = normalized_url
                    
                repo_line = f"deb-{arch} {arch_specific_url} {version} {components}"
                logger.debug(f"Generated repo line: {repo_line}")
                config_lines.append(repo_line)
            
            # Add source packages if enabled (use main mirror for sources)
            if getattr(self.dist_config, 'include_source_packages', False):
                src_line = f"deb-src {normalized_url} {version} {components}"
                config_lines.append(src_line)
        
        # Add additional repositories for Debian and Ubuntu (security, updates, backports)
        if self.dist_config.name == "debian":
            self._add_debian_additional_repos(config_lines, version, components, mirror_urls)
        elif self.dist_config.name == "ubuntu":
            self._add_ubuntu_additional_repos(config_lines, version, components, mirror_urls)
        
        config_lines.append("")
        # Use appropriate clean URL based on version (URLs already clean, no trailing slashes)
        clean_url = mirror_urls[0] if mirror_urls else "http://deb.debian.org/debian"
        config_lines.append(f"clean {clean_url}")
        
        result_config = "\n".join(config_lines)
        logger.debug(f"Generated apt-mirror config for {self.dist_config.name} {version}:\n{result_config}")
        return result_config

    def _add_debian_additional_repos(self, config_lines: List[str], version: str, components: str, mirror_urls: List[str]) -> None:
        """Add Debian-specific additional repositories (security, updates, backports)"""
        # Ensure we're using codenames for Debian
        version = self._get_debian_codename(version)
        archived_versions = ["wheezy", "jessie", "stretch", "buster", "bullseye"]

        # Security repository
        # Only add security repositories for versions with publicly available security updates
        # As of 2025: bullseye (LTS), bookworm, trixie have public security updates
        versions_with_security = ["bullseye", "bookworm", "trixie"]

        if version in versions_with_security:
            # All Debian security updates come from security.debian.org regardless of archive status
            security_url = "http://security.debian.org/debian-security"

            security_suite = f"{version}-security"

            for arch in self.dist_config.architectures:
                # Skip 'all' architecture for wheezy security repos
                if version == "wheezy" and arch == "all":
                    continue
                security_line = f"deb-{arch} {security_url} {security_suite} {components}"
                config_lines.append(security_line)

            if getattr(self.dist_config, 'include_source_packages', False):
                security_src = f"deb-src {security_url} {security_suite} {components}"
                config_lines.append(security_src)

        # Updates repository
        # Most Debian versions have updates (except very old ones)
        versions_with_updates = ["stretch", "buster", "bullseye", "bookworm", "trixie"]

        if version in versions_with_updates:
            if version in archived_versions:
                updates_url = "http://archive.debian.org/debian"
            else:
                updates_url = "http://deb.debian.org/debian"

            updates_suite = f"{version}-updates"

            for arch in self.dist_config.architectures:
                updates_line = f"deb-{arch} {updates_url} {updates_suite} {components}"
                config_lines.append(updates_line)

            if getattr(self.dist_config, 'include_source_packages', False):
                updates_src = f"deb-src {updates_url} {updates_suite} {components}"
                config_lines.append(updates_src)

        # Backports repository (skip for very old versions that didn't have backports)
        if version not in ["wheezy"]:  # wheezy didn't have official backports
            if version in archived_versions:
                backports_url = "http://archive.debian.org/debian"
            else:
                backports_url = "http://deb.debian.org/debian"

            backports_suite = f"{version}-backports"

            for arch in self.dist_config.architectures:
                backports_line = f"deb-{arch} {backports_url} {backports_suite} {components}"
                config_lines.append(backports_line)

            if getattr(self.dist_config, 'include_source_packages', False):
                backports_src = f"deb-src {backports_url} {backports_suite} {components}"
                config_lines.append(backports_src)

    def _add_ubuntu_additional_repos(self, config_lines: List[str], version: str, components: str, mirror_urls: List[str]) -> None:
        """Add Ubuntu-specific additional repositories (security, updates, backports)"""
        eol_versions = ["mantic"]  # Ubuntu versions that have reached EOL

        # Determine base URLs based on version status
        if version in eol_versions:
            base_url = "http://old-releases.ubuntu.com/ubuntu"
            security_url = "http://old-releases.ubuntu.com/ubuntu"
        else:
            base_url = mirror_urls[0].rstrip('/') if mirror_urls else "http://archive.ubuntu.com/ubuntu"
            security_url = "http://security.ubuntu.com/ubuntu"

        # Security repository
        security_suite = f"{version}-security"
        for arch in self.dist_config.architectures:
            # For Ubuntu, ARM architectures use ports.ubuntu.com for security too
            if arch in ["arm64", "armhf"] and "archive.ubuntu.com" in security_url:
                arch_security_url = security_url.replace("archive.ubuntu.com/ubuntu", "ports.ubuntu.com/ubuntu-ports")
            else:
                arch_security_url = security_url

            security_line = f"deb-{arch} {arch_security_url} {security_suite} {components}"
            config_lines.append(security_line)

        if getattr(self.dist_config, 'include_source_packages', False):
            security_src = f"deb-src {security_url} {security_suite} {components}"
            config_lines.append(security_src)

        # Updates repository
        updates_suite = f"{version}-updates"
        for arch in self.dist_config.architectures:
            # For Ubuntu, ARM architectures use ports.ubuntu.com
            if arch in ["arm64", "armhf"] and "archive.ubuntu.com" in base_url:
                arch_base_url = base_url.replace("archive.ubuntu.com/ubuntu", "ports.ubuntu.com/ubuntu-ports")
            else:
                arch_base_url = base_url

            updates_line = f"deb-{arch} {arch_base_url} {updates_suite} {components}"
            config_lines.append(updates_line)

        if getattr(self.dist_config, 'include_source_packages', False):
            updates_src = f"deb-src {base_url} {updates_suite} {components}"
            config_lines.append(updates_src)

        # Backports repository
        backports_suite = f"{version}-backports"
        for arch in self.dist_config.architectures:
            # For Ubuntu, ARM architectures use ports.ubuntu.com
            if arch in ["arm64", "armhf"] and "archive.ubuntu.com" in base_url:
                arch_base_url = base_url.replace("archive.ubuntu.com/ubuntu", "ports.ubuntu.com/ubuntu-ports")
            else:
                arch_base_url = base_url

            backports_line = f"deb-{arch} {arch_base_url} {backports_suite} {components}"
            config_lines.append(backports_line)

        if getattr(self.dist_config, 'include_source_packages', False):
            backports_src = f"deb-src {base_url} {backports_suite} {components}"
            config_lines.append(backports_src)

    def validate_config(self) -> bool:
        required_fields = ['mirror_urls', 'components', 'architectures']
        for field in required_fields:
            if not getattr(self.dist_config, field, None):
                logger.error(f"Missing required field for APT sync: {field}")
                return False
        return True

class YumSyncEngine(SyncEngine):
    def generate_sync_command(self, version: str) -> List[str]:
        repo_name = f"{self.dist_config.name}-{version}"
        
        # Create repository configuration first
        repo_config = self._generate_yum_repo_config(version)
        
        if self.dist_config.name == "rhel":
            # For RHEL, place repo file in standard location and use proper naming
            config_file = f"/tmp/redhat-{version}.repo"
        else:
            # For Rocky/other YUM distros, use existing approach
            config_file = f"/mirror/{repo_name}.repo"
        
        # Get version-specific architectures and repositories
        supported_archs = self._get_supported_architectures(version)
        repositories = self._get_available_repositories(version)
        
        # Debug: Log detected repositories
        repo_names = list(repositories.keys())
        logger.info(f"Detected repositories for {self.dist_config.name} {version}: {repo_names}")
        
        # Create reposync command for each repository and architecture
        commands = []
        createrepo_commands = []
        
        # Create directory structure that matches official Rocky/RHEL layout
        mkdir_commands = []
        # Create temporary sync directory
        mkdir_commands.append("mkdir -p /tmp/sync")
        
        # Create directories for all repositories and architectures with proper permissions
        for arch in supported_archs:
            for repo_id, repo_info in repositories.items():
                mkdir_commands.append(f"mkdir -p /mirror/{version}/{repo_info['path']}/{arch}/os && chmod -R 777 /mirror/{version}/{repo_info['path']}/{arch}")
        
        # Generate sync commands for all repositories
        for arch in supported_archs:
            if self.dist_config.name == "rhel":
                # RHEL doesn't iterate over mirror_urls since we use specific CDN URLs
                for repo_id, repo_info in repositories.items():
                    repo_path = f"/mirror/{version}/{repo_info['path']}/{arch}/os"
                    repo_tmp = f"/tmp/sync/rhel-{version}-{repo_id.lower()}-rpms-{arch}"
                    
                    # RHEL sync command with entitlement authentication
                    # Include both the specific architecture and noarch packages
                    cmd = f"""
                    echo "Installing required packages for RHEL sync..." &&
                    dnf install -y dnf-plugins-core rsync &&
                    echo "Starting RHEL sync for {repo_id} {arch}..." &&
                    dnf reposync --config={config_file} --repoid=rhel-{version}-{repo_id.lower()}-rpms-{arch} --arch={arch} --arch=noarch -p /tmp/sync --download-metadata --downloadcomps --newest-only --verbose 2>&1 &&
                    echo "Sync completed, checking results..." &&
                    ls -la /tmp/sync/ &&
                    if [ -d {repo_tmp} ]; then
                        echo "Found sync directory {repo_tmp}" &&
                        ls -la {repo_tmp}/ &&
                        if [ -n "$(ls -A {repo_tmp} 2>/dev/null)" ]; then
                            echo "Syncing files to {repo_path} with rsync..." &&
                            rsync -av --delete-after {repo_tmp}/ {repo_path}/ &&
                            rm -rf {repo_tmp} &&
                            echo "Successfully synced {repo_id} {arch}"
                        else
                            echo "No files in {repo_tmp}, cleaning up..." &&
                            rm -rf {repo_tmp}
                        fi
                    else
                        echo "Sync directory {repo_tmp} not found, may have failed"
                    fi
                    """.strip()
                    commands.append(cmd)
                    logger.info(f"Added sync command for {repo_id} (command #{len(commands)})")
                    
                    # Create repository metadata (only if directory has content)
                    createrepo_commands.append(f"[ -n \"$(ls -A {repo_path} 2>/dev/null)\" ] && createrepo_c {repo_path} || echo \"Skipping createrepo for empty {repo_path}\"")
            else:
                # Rocky Linux and other YUM distributions
                for mirror_url in self.dist_config.mirror_urls:
                    for repo_id, repo_info in repositories.items():
                        try:
                            logger.info(f"Processing repository: {repo_id} for {self.dist_config.name} {version} {arch}")
                            repo_path = f"/mirror/{version}/{repo_info['path']}/{arch}/os"
                            repo_tmp = f"/tmp/sync/{repo_name}-{repo_id}-{arch}"
                            
                            # Standard sync command
                            # Include both the specific architecture and noarch packages
                            cmd = f"""
                            echo "Installing rsync for safe file transfers..." &&
                            dnf install -y rsync &&
                            echo "Starting sync for {repo_id} {arch}..." &&
                            dnf reposync --config={config_file} --repoid={repo_name}-{repo_id}-{arch} --arch={arch} --arch=noarch -p /tmp/sync --download-metadata --downloadcomps --newest-only --verbose 2>&1 &&
                        echo "Sync completed, checking results..." &&
                        ls -la /tmp/sync/ &&
                        if [ -d {repo_tmp} ]; then
                            echo "Found sync directory {repo_tmp}" &&
                            ls -la {repo_tmp}/ &&
                            if [ -n "$(ls -A {repo_tmp} 2>/dev/null)" ]; then
                                echo "Syncing files to {repo_path} with rsync..." &&
                                rsync -av --delete-after {repo_tmp}/ {repo_path}/ &&
                                rm -rf {repo_tmp} &&
                                echo "Successfully synced {repo_id} {arch}"
                            else
                                echo "No files in {repo_tmp}, cleaning up..." &&
                                rm -rf {repo_tmp}
                            fi
                        else
                            echo "Sync directory {repo_tmp} not found, may have failed"
                        fi
                        """.strip()
                            commands.append(cmd)
                            logger.info(f"Added sync command for {repo_id} (command #{len(commands)})")
                        except Exception as e:
                            logger.error(f"Failed to generate command for {repo_id}: {e}")
                            continue
                    
                    # Create repository metadata (only if directory has content)
                    createrepo_commands.append(f"[ -n \"$(ls -A {repo_path} 2>/dev/null)\" ] && createrepo_c {repo_path} || echo \"Skipping createrepo for empty {repo_path}\"")
        
        # Add ISO download commands
        iso_commands = self._generate_iso_download_commands(version)
        commands.extend(iso_commands)
        
        # Debug: Log total commands generated
        logger.info(f"Generated {len(commands)} sync commands for {self.dist_config.name} {version}")
        
        # Escape the repo config for shell
        escaped_config = repo_config.replace('"', '\\"').replace('\n', '\\n')
        
        if self.dist_config.name == "rhel":
            # For RHEL, we need to dynamically find and configure entitlement certificates
            full_command = f'''
            echo "Finding entitlement certificates..." &&
            CERT_FILE=$(find /etc/pki/entitlement -name "*.pem" -not -name "*-key.pem" | head -1) &&
            KEY_FILE=$(find /etc/pki/entitlement -name "*-key.pem" | head -1) &&
            if [ -z "$CERT_FILE" ] || [ -z "$KEY_FILE" ]; then
                echo "Error: Could not find entitlement certificates in /etc/pki/entitlement/"
                exit 1
            fi &&
            echo "Using certificate: $CERT_FILE" &&
            echo "Using key: $KEY_FILE" &&
            echo -e "{escaped_config}" | sed "s|sslclientcert=/etc/pki/entitlement/|sslclientcert=$CERT_FILE|g; s|sslclientkey=/etc/pki/entitlement/|sslclientkey=$KEY_FILE|g" > {config_file} &&
            {' && '.join(mkdir_commands)} &&
            {'; '.join(commands)} &&
            {' && '.join(createrepo_commands)}
            '''
        elif self.dist_config.name == "epel":
            # For EPEL, we need to download and install GPG keys first
            gpg_key_commands = []
            if hasattr(self.dist_config, 'gpg_key_urls') and self.dist_config.gpg_key_urls:
                for gpg_url in self.dist_config.gpg_key_urls:
                    key_filename = gpg_url.split('/')[-1]  # Extract filename from URL
                    gpg_key_commands.append(f"curl -sL {gpg_url} -o /etc/pki/rpm-gpg/{key_filename}")

            gpg_setup = ' && '.join(gpg_key_commands) if gpg_key_commands else 'echo "No GPG keys to install"'

            full_command = f'''
            echo "Setting up EPEL GPG keys..." &&
            mkdir -p /etc/pki/rpm-gpg &&
            {gpg_setup} &&
            echo -e "{escaped_config}" > {config_file} &&
            {' && '.join(mkdir_commands)} &&
            {'; '.join(commands)} &&
            {' && '.join(createrepo_commands)}
            '''
        else:
            full_command = f'''
            echo -e "{escaped_config}" > {config_file} &&
            {' && '.join(mkdir_commands)} &&
            {'; '.join(commands)} &&
            {' && '.join(createrepo_commands)}
            '''
        
        return ['sh', '-c', full_command]
    
    def _get_supported_architectures(self, version: str) -> List[str]:
        """Filter architectures based on version support for Rocky Linux, RHEL, and EPEL."""
        all_archs = self.dist_config.architectures

        # Rocky Linux and RHEL architecture support by version
        if self.dist_config.name in ["rocky", "rhel"]:
            if version == "8":
                # Rocky/RHEL 8 only supports x86_64 and aarch64
                return [arch for arch in all_archs if arch in ["x86_64", "aarch64"]]
            elif version == "9":
                # Rocky/RHEL 9 supports x86_64, aarch64, ppc64le, s390x
                return [arch for arch in all_archs if arch in ["x86_64", "aarch64", "ppc64le", "s390x"]]
            elif version == "10":
                # Rocky/RHEL 10 supports x86_64, aarch64, ppc64le, s390x, riscv64
                return [arch for arch in all_archs if arch in ["x86_64", "aarch64", "ppc64le", "s390x", "riscv64"]]
        elif self.dist_config.name == "epel":
            # EPEL architecture support by version
            if version == "8":
                # EPEL 8 supports x86_64, aarch64, ppc64le, s390x
                return [arch for arch in all_archs if arch in ["x86_64", "aarch64", "ppc64le", "s390x"]]
            elif version in ["9", "10"]:
                # EPEL 9+ supports x86_64, aarch64, ppc64le, s390x
                return [arch for arch in all_archs if arch in ["x86_64", "aarch64", "ppc64le", "s390x"]]

        # For other distributions, return all architectures
        return all_archs

    def _get_available_repositories(self, version: str) -> Dict[str, Dict[str, str]]:
        """Get all available repositories based on configuration components"""
        repositories = {}
        
        # Use components from configuration if available
        if hasattr(self.dist_config, 'components') and self.dist_config.components:
            for component in self.dist_config.components:
                # Handle special cases for repository naming
                if component == "PowerTools":
                    # PowerTools for version 8, CRB for versions 9+
                    if version == "8":
                        repositories["powertools"] = {"name": component, "path": component}
                    # Skip PowerTools for versions 9+ as it's replaced by CRB
                elif component == "CRB":
                    # CRB only available in versions 9+
                    if version in ["9", "10"]:
                        repositories["crb"] = {"name": component, "path": component}
                    # Skip CRB for version 8 as it uses PowerTools
                elif component == "ResilientStorage":
                    # ResilientStorage only available in Rocky/RHEL 9
                    if version == "9":
                        repositories["rs"] = {"name": component, "path": component}
                    # Skip for other versions
                elif component == "SAPHANA":
                    # SAP HANA only available in versions 9+
                    if version in ["9", "10"]:
                        repositories["saphana"] = {"name": component, "path": component}
                elif component == "SAP":
                    # SAP only available in versions 9+
                    if version in ["9", "10"]:
                        repositories["sap"] = {"name": component, "path": component}
                elif component == "HighAvailability":
                    repositories["ha"] = {"name": component, "path": component}
                else:
                    # Standard components - use lowercase for repo key
                    repo_key = component.lower()
                    repositories[repo_key] = {"name": component, "path": component}
        else:
            # Fallback to minimal default repositories if no components configured
            repositories = {
                "baseos": {"name": "BaseOS", "path": "BaseOS"},
                "appstream": {"name": "AppStream", "path": "AppStream"}
            }
        
        return repositories

    def _generate_iso_download_commands(self, version: str) -> List[str]:
        """Generate commands to download ISO images for YUM distributions with SHA256 verification"""
        if self.dist_config.name not in ["rocky", "rhel"]:
            return []
        
        # Use configured ISO architectures or fall back to repository architectures
        iso_archs = getattr(self.dist_config, 'iso_architectures', None) or self.dist_config.architectures
        if not iso_archs:
            return []
        
        iso_commands = []
        
        # Create ISO directory with proper permissions
        iso_commands.append("mkdir -p /mirror/isos && chmod 777 /mirror/isos")
        
        for mirror_url in self.dist_config.mirror_urls:
            # Normalize URL by ensuring exactly one trailing slash
            normalized_url = mirror_url.rstrip('/') + '/'
            for arch in iso_archs:
                iso_base_url = f"{normalized_url}{version}/isos/{arch}/"
                
                # Download and verify checksums before downloading ISOs
                checksum_verification_commands = self._generate_iso_checksum_verification(iso_base_url, version, arch)
                iso_commands.extend(checksum_verification_commands)
        
        return iso_commands
    
    def _generate_iso_checksum_verification(self, iso_base_url: str, version: str, arch: str) -> List[str]:
        """Generate commands to verify ISO checksums and only download if needed"""
        commands = []
        
        if self.dist_config.name == "rocky":
            # Rocky Linux uses a single CHECKSUM file - create a proper shell script
            checksum_url = f"{iso_base_url}CHECKSUM"
            
            # Create a shell script for checksum verification
            verification_script = f'''
echo 'Downloading checksum file for Rocky {version} {arch}...'
if wget -q -O /tmp/CHECKSUM_{version}_{arch} {checksum_url}; then
  echo 'Processing checksums for Rocky {version} {arch}...'
  while IFS=' ' read -r checksum filename; do
    if [[ "$filename" == *".iso" ]]; then
      local_file="/mirror/isos/$filename"
      if [ -f "$local_file" ]; then
        echo "Verifying existing ISO: $filename"
        local_checksum=$(sha256sum "$local_file" | cut -d' ' -f1)
        if [ "$local_checksum" = "$checksum" ]; then
          echo "✓ $filename: Checksum matches, skipping download"
        else
          echo "✗ $filename: Checksum mismatch, re-downloading"
          wget -O "$local_file" "{iso_base_url}$filename" || echo "Failed to download $filename"
        fi
      else
        echo "Downloading new ISO: $filename"
        wget -O "$local_file" "{iso_base_url}$filename" || echo "Failed to download $filename"
      fi
    fi
  done < /tmp/CHECKSUM_{version}_{arch}
  rm -f /tmp/CHECKSUM_{version}_{arch}
else
  echo 'Warning: Could not download CHECKSUM file, falling back to pattern-based download'
  wget -r -l1 -nd -A 'Rocky-{version}*.iso' -P /mirror/isos/ {iso_base_url} || echo 'No ISOs found for {arch} or download failed'
fi
            '''.strip()
            
            commands.append(verification_script)
        
        elif self.dist_config.name == "rhel":
            # RHEL may not have public CHECKSUM files, use timestamp-based checking
            commands.extend([
                f"echo 'RHEL ISO verification - using timestamp-based checking'",
                f"wget -r -l1 -nd -N -A 'rhel-{version}*.iso' -P /mirror/isos/ {iso_base_url} || echo 'No ISOs found for {arch} or download failed'"
            ])
        
        return commands
    
    def _generate_yum_repo_config(self, version: str) -> str:
        repo_name = f"{self.dist_config.name}-{version}"
        config_lines = []
        
        # Get version-specific architectures
        supported_archs = self._get_supported_architectures(version)
        
        # Define all available repositories by version
        repositories = self._get_available_repositories(version)
        
        if self.dist_config.name == "rhel":
            # Generate RHEL-specific repo configuration with entitlement authentication
            for arch in supported_archs:
                for repo_id, repo_info in repositories.items():
                    config_lines.extend([
                        f"[rhel-{version}-{repo_id.lower()}-rpms-{arch}]",
                        f"name=Red Hat Enterprise Linux {version} - {repo_info['name']} (RPMs) ({arch})",
                        f"baseurl=https://cdn.redhat.com/content/dist/rhel{version}/{version}/{arch}/{repo_id.lower()}/os",
                        "enabled=1",
                        "gpgcheck=1",
                        "gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release",
                        "sslverify=1",
                        "sslcacert=/etc/rhsm/ca/redhat-uep.pem",
                        "sslclientkey=/etc/pki/entitlement/",
                        "sslclientcert=/etc/pki/entitlement/",
                        ""  # Empty line between sections
                    ])
        elif self.dist_config.name == "epel":
            # Generate EPEL-specific repo configuration
            for arch in supported_archs:
                for mirror_url in self.dist_config.mirror_urls:
                    # Normalize URL by ensuring exactly one trailing slash
                    normalized_url = mirror_url.rstrip('/') + '/'
                    for repo_id, repo_info in repositories.items():
                        config_lines.extend([
                            f"[{repo_name}-{repo_id}-{arch}]",
                            f"name=Extra Packages for Enterprise Linux {version} - {repo_info['name']} ({arch})",
                            f"baseurl={normalized_url}{version}/{repo_info['path']}/{arch}/",
                            "enabled=1",
                            "gpgcheck=1",
                            f"gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-EPEL-{version}",
                            ""  # Empty line between sections
                        ])
        else:
            # Generate standard repo configuration for Rocky/other YUM distros
            for arch in supported_archs:
                for mirror_url in self.dist_config.mirror_urls:
                    # Normalize URL by ensuring exactly one trailing slash
                    normalized_url = mirror_url.rstrip('/') + '/'
                    for repo_id, repo_info in repositories.items():
                        config_lines.extend([
                            f"[{repo_name}-{repo_id}-{arch}]",
                            f"name={self.dist_config.name.title()} {version} - {repo_info['name']} ({arch})",
                            f"baseurl={normalized_url}{version}/{repo_info['path']}/{arch}/os/",
                            "enabled=1",
                            "gpgcheck=0",  # Disable gpgcheck for now to avoid key issues
                            ""  # Empty line between sections
                        ])
        
        return "\n".join(config_lines)
    
    def validate_config(self) -> bool:
        required_fields = ['mirror_urls', 'architectures']
        for field in required_fields:
            if not getattr(self.dist_config, field, None):
                logger.error(f"Missing required field for YUM sync: {field}")
                return False
        return True

class SyncManager:
    def __init__(self, orchestrator: ContainerOrchestrator):
        self.orchestrator = orchestrator
        self._engines: Dict[str, SyncEngine] = {}
        # APT-mirror can't handle concurrent access, so limit to 1
        self._apt_semaphore = asyncio.Semaphore(1)
        # YUM repos can run in parallel - allow enough slots for all YUM versions
        self._yum_semaphore = asyncio.Semaphore(10)
    
    def get_engine(self, dist_config: DistributionConfig) -> SyncEngine:
        engine_key = f"{dist_config.name}-{dist_config.type}"
        
        if engine_key not in self._engines:
            if dist_config.type == "apt":
                self._engines[engine_key] = AptSyncEngine(dist_config, self.orchestrator)
            elif dist_config.type == "yum":
                self._engines[engine_key] = YumSyncEngine(dist_config, self.orchestrator)
            else:
                raise ValueError(f"Unknown repository type: {dist_config.type}")
        
        return self._engines[engine_key]
    
    async def sync_distribution(self, dist_config: DistributionConfig, versions: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        if not dist_config.enabled:
            logger.warning(f"Distribution {dist_config.name} is disabled, skipping sync")
            return []
        
        sync_versions = versions or dist_config.versions
        engine = self.get_engine(dist_config)
        
        # Choose appropriate semaphore based on distribution type
        semaphore = self._apt_semaphore if dist_config.type == "apt" else self._yum_semaphore
        
        async def sync_single_version(version: str):
            async with semaphore:
                logger.info(f"Starting sync for {dist_config.name} {version} (type: {dist_config.type})")
                return await engine.sync_version(version)
        
        # For APT distributions, run sequentially to avoid apt-mirror locking issues
        if dist_config.type == "apt":
            logger.info(f"Running APT sync sequentially for {dist_config.name}")
            results = []
            for version in sync_versions:
                try:
                    result = await sync_single_version(version)
                    results.append(result)
                except Exception as e:
                    logger.error(f"APT sync failed for {dist_config.name} {version}: {e}")
                    results.append({
                        'distribution': dist_config.name,
                        'version': version,
                        'status': 'failed',
                        'error': str(e)
                    })
            return results
        
        else:
            # For YUM distributions, run in parallel (but still limited by semaphore)
            logger.info(f"Running YUM sync in parallel for {dist_config.name}")
            tasks = [sync_single_version(version) for version in sync_versions]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Convert exceptions to error results
            final_results = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    final_results.append({
                        'distribution': dist_config.name,
                        'version': sync_versions[i],
                        'status': 'failed',
                        'error': str(result)
                    })
                else:
                    final_results.append(result)
            
            return final_results
    
    async def sync_multiple_distributions(self, distributions: Dict[str, List[str]]) -> List[Dict[str, Any]]:
        """
        Sync multiple distributions with proper concurrency control.
        APT distributions run sequentially, YUM distributions can run in parallel.
        """
        all_results = []
        
        # Separate APT and YUM distributions
        apt_distributions = {}
        yum_distributions = {}
        
        for dist_name, versions in distributions.items():
            config = self.orchestrator.config_manager.get_config().distributions.get(dist_name)
            if config and config.enabled:
                if config.type == "apt":
                    apt_distributions[dist_name] = versions
                else:
                    yum_distributions[dist_name] = versions
        
        # Start YUM distributions in parallel immediately (don't wait for APT)
        # Create individual tasks for each distribution+version combination
        yum_tasks = []
        if yum_distributions:
            logger.info(f"Starting YUM distributions in parallel: {list(yum_distributions.keys())}")
            for dist_name, versions in yum_distributions.items():
                dist_config = self.orchestrator.config_manager.get_config().distributions[dist_name]
                # Create a separate task for each version to allow true parallelism
                for version in versions:
                    task = asyncio.create_task(self.sync_distribution(dist_config, [version]))
                    yum_tasks.append(task)
            
            # Give YUM tasks a chance to start before APT begins
            await asyncio.sleep(0.1)
        
        # Run APT distributions sequentially (one at a time) while YUM runs in parallel
        if apt_distributions:
            logger.info(f"Starting APT distributions sequentially: {list(apt_distributions.keys())}")
            for dist_name, versions in apt_distributions.items():
                dist_config = self.orchestrator.config_manager.get_config().distributions[dist_name]
                logger.info(f"Starting APT sync for {dist_name}")
                results = await self.sync_distribution(dist_config, versions)
                all_results.extend(results)
                logger.info(f"Completed APT sync for {dist_name}")
        
        # Wait for all YUM distributions to complete
        if yum_tasks:
            logger.info("Waiting for YUM distributions to complete...")
            yum_results = await asyncio.gather(*yum_tasks, return_exceptions=True)
            for result_list in yum_results:
                if isinstance(result_list, Exception):
                    logger.error(f"YUM sync failed: {result_list}")
                else:
                    all_results.extend(result_list)
        
        return all_results