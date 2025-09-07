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
        
        # Add repository lines for each architecture
        for mirror_url in self.dist_config.mirror_urls:
            for arch in self.dist_config.architectures:
                components = " ".join(self.dist_config.components)
                repo_line = f"deb-{arch} {mirror_url} {version} {components}"
                config_lines.append(repo_line)
            
            # Add source packages if enabled
            if getattr(self.dist_config, 'include_source_packages', False):
                components = " ".join(self.dist_config.components)
                src_line = f"deb-src {mirror_url} {version} {components}"
                config_lines.append(src_line)
        
        config_lines.append("")
        config_lines.append("clean http://deb.debian.org/debian")
        
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
        config_file = f"/mirror/{repo_name}.repo"
        
        # Get version-specific architectures
        supported_archs = self._get_supported_architectures(version)
        
        # Create reposync command for each architecture using the config file
        commands = []
        createrepo_commands = []
        
        # Create directory structure that matches official Rocky/RHEL layout
        mkdir_commands = []
        # Create temporary sync directory
        mkdir_commands.append("mkdir -p /tmp/sync")
        
        for arch in supported_archs:
            # Create BaseOS and AppStream directories for each architecture
            mkdir_commands.append(f"mkdir -p /mirror/{version}/BaseOS/{arch}/os")
            mkdir_commands.append(f"mkdir -p /mirror/{version}/AppStream/{arch}/os")
        
        for arch in supported_archs:
            for mirror_url in self.dist_config.mirror_urls:
                # BaseOS repository - sync to proper path
                baseos_path = f"/mirror/{version}/BaseOS/{arch}/os"
                # reposync creates a subdirectory with repo name, so we sync to parent and then move
                cmd = f"dnf reposync --config={config_file} --repoid={repo_name}-baseos-{arch} --arch={arch} -p /tmp/sync --download-metadata && mv /tmp/sync/{repo_name}-baseos-{arch}/* {baseos_path}/ && rm -rf /tmp/sync/{repo_name}-baseos-{arch}"
                commands.append(cmd)
                
                # Create repository metadata for BaseOS
                createrepo_commands.append(f"createrepo_c {baseos_path}")
                
                # AppStream repository - sync to proper path
                appstream_path = f"/mirror/{version}/AppStream/{arch}/os"
                # reposync creates a subdirectory with repo name, so we sync to parent and then move
                appstream_cmd = f"dnf reposync --config={config_file} --repoid={repo_name}-appstream-{arch} --arch={arch} -p /tmp/sync --download-metadata && mv /tmp/sync/{repo_name}-appstream-{arch}/* {appstream_path}/ && rm -rf /tmp/sync/{repo_name}-appstream-{arch}"
                commands.append(appstream_cmd)
                
                # Create repository metadata for AppStream
                createrepo_commands.append(f"createrepo_c {appstream_path}")
        
        # Escape the repo config for shell
        escaped_config = repo_config.replace('"', '\\"').replace('\n', '\\n')
        
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
            elif version in ["9", "10"]:
                # Rocky/RHEL 9 and 10 support all listed architectures
                return all_archs
        
        # For other distributions, return all architectures
        return all_archs
    
    def _generate_yum_repo_config(self, version: str) -> str:
        repo_name = f"{self.dist_config.name}-{version}"
        config_lines = []
        
        # Get version-specific architectures
        supported_archs = self._get_supported_architectures(version)
        
        for arch in supported_archs:
            for mirror_url in self.dist_config.mirror_urls:
                # BaseOS repository
                config_lines.extend([
                    f"[{repo_name}-baseos-{arch}]",
                    f"name={self.dist_config.name.title()} {version} - BaseOS ({arch})",
                    f"baseurl={mirror_url}{version}/BaseOS/{arch}/os/",
                    "enabled=1",
                    "gpgcheck=0",  # Disable gpgcheck for now to avoid key issues
                    ""  # Empty line between sections
                ])
                
                # AppStream repository
                config_lines.extend([
                    f"[{repo_name}-appstream-{arch}]",
                    f"name={self.dist_config.name.title()} {version} - AppStream ({arch})",
                    f"baseurl={mirror_url}{version}/AppStream/{arch}/os/",
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
        
        # Run APT distributions sequentially (one at a time)
        logger.info(f"Syncing APT distributions sequentially: {list(apt_distributions.keys())}")
        for dist_name, versions in apt_distributions.items():
            dist_config = self.orchestrator.config_manager.get_config().distributions[dist_name]
            logger.info(f"Starting APT sync for {dist_name}")
            results = await self.sync_distribution(dist_config, versions)
            all_results.extend(results)
            logger.info(f"Completed APT sync for {dist_name}")
        
        # Run YUM distributions in parallel
        if yum_distributions:
            logger.info(f"Syncing YUM distributions in parallel: {list(yum_distributions.keys())}")
            yum_tasks = []
            for dist_name, versions in yum_distributions.items():
                dist_config = self.orchestrator.config_manager.get_config().distributions[dist_name]
                task = self.sync_distribution(dist_config, versions)
                yum_tasks.append(task)
            
            yum_results = await asyncio.gather(*yum_tasks, return_exceptions=True)
            for result_list in yum_results:
                if isinstance(result_list, Exception):
                    logger.error(f"YUM sync failed: {result_list}")
                else:
                    all_results.extend(result_list)
        
        return all_results