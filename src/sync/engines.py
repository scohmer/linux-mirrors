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
            archived_versions = ["wheezy", "jessie", "stretch", "buster"]
            if version in archived_versions:
                return ["http://archive.debian.org/debian/"]
            else:
                # Use current Debian mirrors for bullseye, bookworm, trixie, etc.
                return self.dist_config.mirror_urls
        
        # For other distributions, use configured mirror URLs
        return self.dist_config.mirror_urls

    def _generate_apt_mirror_config(self, version: str) -> str:
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
        components = " ".join(self.dist_config.components)
        for mirror_url in mirror_urls:
            for arch in self.dist_config.architectures:
                repo_line = f"deb-{arch} {mirror_url} {version} {components}"
                config_lines.append(repo_line)
            
            # Add source packages if enabled
            if getattr(self.dist_config, 'include_source_packages', False):
                src_line = f"deb-src {mirror_url} {version} {components}"
                config_lines.append(src_line)
        
        # Add Debian-specific security and backports repositories
        if self.dist_config.name == "debian":
            archived_versions = ["wheezy", "jessie", "stretch", "buster", "bullseye"]
            
            # Only add security repositories for versions with publicly available security updates
            # As of 2025: bullseye (LTS), bookworm, trixie have public security updates
            # stretch/buster only have commercial ELTS, wheezy/jessie are EOL
            versions_with_security = ["bullseye", "bookworm", "trixie"]
            
            if version in versions_with_security:
                # Security repository
                if version in archived_versions:
                    # Archived versions with security (currently bullseye LTS)
                    security_url = "http://archive.debian.org/debian-security/"
                    security_suite = f"{version}-security"
                else:
                    # Current versions use security.debian.org
                    security_url = "http://security.debian.org/debian-security/"
                    security_suite = f"{version}-security"
                
                # Add security repository lines
                for arch in self.dist_config.architectures:
                    security_line = f"deb-{arch} {security_url} {security_suite} {components}"
                    config_lines.append(security_line)
                
                if getattr(self.dist_config, 'include_source_packages', False):
                    security_src = f"deb-src {security_url} {security_suite} {components}"
                    config_lines.append(security_src)
            
            # Backports repository (skip for very old versions that didn't have backports)
            if version not in ["wheezy"]:  # wheezy didn't have official backports
                if version in archived_versions:
                    # Archived backports use archive.debian.org
                    backports_url = "http://archive.debian.org/debian/"
                else:
                    # Current backports use deb.debian.org
                    backports_url = "http://deb.debian.org/debian/"
                
                backports_suite = f"{version}-backports"
                
                # Add backports repository lines
                for arch in self.dist_config.architectures:
                    backports_line = f"deb-{arch} {backports_url} {backports_suite} {components}"
                    config_lines.append(backports_line)
                
                if getattr(self.dist_config, 'include_source_packages', False):
                    backports_src = f"deb-src {backports_url} {backports_suite} {components}"
                    config_lines.append(backports_src)
        
        config_lines.append("")
        # Use appropriate clean URL based on version
        clean_url = mirror_urls[0] if mirror_urls else "http://deb.debian.org/debian/"
        config_lines.append(f"clean {clean_url}")
        
        return "\n".join(config_lines)  # Fix: use actual newlines
    
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
                    cmd = f"""
                    echo "Installing dnf-plugins-core for RHEL sync..." &&
                    dnf install -y dnf-plugins-core &&
                    echo "Starting RHEL sync for {repo_id} {arch}..." &&
                    dnf reposync --config={config_file} --repoid=rhel-{version}-{repo_id.lower()}-rpms-{arch} --arch={arch} -p /tmp/sync --download-metadata --verbose 2>&1 &&
                    echo "Sync completed, checking results..." &&
                    ls -la /tmp/sync/ &&
                    if [ -d {repo_tmp} ]; then
                        echo "Found sync directory {repo_tmp}" &&
                        ls -la {repo_tmp}/ &&
                        if [ -n "$(ls -A {repo_tmp} 2>/dev/null)" ]; then
                            echo "Moving files to {repo_path}..." &&
                            mv {repo_tmp}/* {repo_path}/ &&
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
                    
                    # Create repository metadata (only if directory has content)
                    createrepo_commands.append(f"[ -n \"$(ls -A {repo_path} 2>/dev/null)\" ] && createrepo_c {repo_path} || echo \"Skipping createrepo for empty {repo_path}\"")
            else:
                # Rocky Linux and other YUM distributions
                for mirror_url in self.dist_config.mirror_urls:
                    for repo_id, repo_info in repositories.items():
                        repo_path = f"/mirror/{version}/{repo_info['path']}/{arch}/os"
                        repo_tmp = f"/tmp/sync/{repo_name}-{repo_id}-{arch}"
                        
                        # Standard sync command
                        cmd = f"""
                        echo "Starting sync for {repo_id} {arch}..." &&
                        dnf reposync --config={config_file} --repoid={repo_name}-{repo_id}-{arch} --arch={arch} -p /tmp/sync --download-metadata --verbose 2>&1 &&
                    echo "Sync completed, checking results..." &&
                    ls -la /tmp/sync/ &&
                    if [ -d {repo_tmp} ]; then
                        echo "Found sync directory {repo_tmp}" &&
                        ls -la {repo_tmp}/ &&
                        if [ -n "$(ls -A {repo_tmp} 2>/dev/null)" ]; then
                            echo "Moving files to {repo_path}..." &&
                            mv {repo_tmp}/* {repo_path}/ &&
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
                    
                    # Create repository metadata (only if directory has content)
                    createrepo_commands.append(f"[ -n \"$(ls -A {repo_path} 2>/dev/null)\" ] && createrepo_c {repo_path} || echo \"Skipping createrepo for empty {repo_path}\"")
        
        # Add ISO download commands
        iso_commands = self._generate_iso_download_commands(version, supported_archs)
        commands.extend(iso_commands)
        
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
            {' && '.join(commands)} &&
            {' && '.join(createrepo_commands)}
            '''
        else:
            full_command = f'''
            echo -e "{escaped_config}" > {config_file} &&
            {' && '.join(mkdir_commands)} &&
            {' && '.join(commands)} &&
            {' && '.join(createrepo_commands)}
            '''
        
        return ['sh', '-c', full_command]
    
    def _get_supported_architectures(self, version: str) -> List[str]:
        """Filter architectures based on version support for Rocky Linux and RHEL."""
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
        
        # For other distributions, return all architectures
        return all_archs

    def _get_available_repositories(self, version: str) -> Dict[str, Dict[str, str]]:
        """Get all available repositories for a specific version"""
        if self.dist_config.name in ["rocky", "rhel"]:
            if version == "8":
                return {
                    "baseos": {"name": "BaseOS", "path": "BaseOS"},
                    "appstream": {"name": "AppStream", "path": "AppStream"},
                    "powertools": {"name": "PowerTools", "path": "PowerTools"},
                    "extras": {"name": "Extras", "path": "extras"},
                    "devel": {"name": "Devel", "path": "Devel"},
                    "plus": {"name": "Plus", "path": "plus"},
                    "ha": {"name": "HighAvailability", "path": "HighAvailability"},
                    "rs": {"name": "ResilientStorage", "path": "ResilientStorage"},
                    "rt": {"name": "RT", "path": "RT"},
                    "nfv": {"name": "NFV", "path": "NFV"}
                }
            elif version in ["9", "10"]:
                repos = {
                    "baseos": {"name": "BaseOS", "path": "BaseOS"},
                    "appstream": {"name": "AppStream", "path": "AppStream"},
                    "crb": {"name": "CRB", "path": "CRB"},
                    "extras": {"name": "Extras", "path": "extras"},
                    "devel": {"name": "Devel", "path": "devel"},
                    "plus": {"name": "Plus", "path": "plus"},
                    "ha": {"name": "HighAvailability", "path": "HighAvailability"},
                    "rt": {"name": "RT", "path": "RT"},
                    "nfv": {"name": "NFV", "path": "NFV"},
                    "sap": {"name": "SAP", "path": "SAP"},
                    "saphana": {"name": "SAPHANA", "path": "SAPHANA"}
                }
                # ResilientStorage only available in Rocky 9
                if version == "9":
                    repos["rs"] = {"name": "ResilientStorage", "path": "ResilientStorage"}
                return repos
        
        # Default fallback for other distributions
        return {
            "baseos": {"name": "BaseOS", "path": "BaseOS"},
            "appstream": {"name": "AppStream", "path": "AppStream"}
        }

    def _generate_iso_download_commands(self, version: str, supported_archs: List[str]) -> List[str]:
        """Generate commands to download ISO images for YUM distributions"""
        if self.dist_config.name not in ["rocky", "rhel"]:
            return []
        
        iso_commands = []
        
        # Create ISO directory with proper permissions
        iso_commands.append("mkdir -p /mirror/isos && chmod 777 /mirror/isos")
        
        for mirror_url in self.dist_config.mirror_urls:
            for arch in supported_archs:
                # Download boot ISOs and DVD ISOs
                iso_base_url = f"{mirror_url}{version}/isos/{arch}/"
                
                # Common ISO patterns for Rocky Linux
                iso_patterns = [
                    f"Rocky-{version}*.iso",  # All ISOs for the version
                ]
                
                for pattern in iso_patterns:
                    # Use wget to download ISO files with pattern matching
                    iso_cmd = f"wget -r -l1 -nd -A '{pattern}' -P /mirror/isos/ {iso_base_url} || echo 'No ISOs found for {arch} or download failed'"
                    iso_commands.append(iso_cmd)
        
        return iso_commands
    
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
        else:
            # Generate standard repo configuration for Rocky/other YUM distros
            for arch in supported_archs:
                for mirror_url in self.dist_config.mirror_urls:
                    for repo_id, repo_info in repositories.items():
                        config_lines.extend([
                            f"[{repo_name}-{repo_id}-{arch}]",
                            f"name={self.dist_config.name.title()} {version} - {repo_info['name']} ({arch})",
                            f"baseurl={mirror_url}{version}/{repo_info['path']}/{arch}/os/",
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
        # YUM repos can run in parallel
        self._yum_semaphore = asyncio.Semaphore(3)
    
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