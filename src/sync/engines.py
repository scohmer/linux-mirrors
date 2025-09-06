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
        
        # Add repository lines
        for mirror_url in self.dist_config.mirror_urls:
            for arch in self.dist_config.architectures:
                components = " ".join(self.dist_config.components)
                repo_line = f"deb-{arch} {mirror_url} {version} {components}"
                config_lines.append(repo_line)
        
        config_lines.append("")
        config_lines.append("clean http://deb.debian.org/debian")
        
        return "\\n".join(config_lines)
    
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
        
        # Create reposync command for each architecture
        commands = []
        for arch in self.dist_config.architectures:
            for mirror_url in self.dist_config.mirror_urls:
                repo_url = f"{mirror_url}{version}/BaseOS/{arch}/os/"
                cmd = f"reposync --gpgcheck --plugins --repoid={repo_name}-baseos-{arch} --arch={arch} --download_path=/mirror/{arch} --downloadcomps --download-metadata"
                commands.append(cmd)
                
                # Add AppStream repository
                appstream_url = f"{mirror_url}{version}/AppStream/{arch}/os/"
                appstream_cmd = f"reposync --gpgcheck --plugins --repoid={repo_name}-appstream-{arch} --arch={arch} --download_path=/mirror/{arch} --downloadcomps --download-metadata"
                commands.append(appstream_cmd)
        
        # Create repository configuration first
        repo_config = self._generate_yum_repo_config(version)
        
        full_command = f'''
        echo "{repo_config}" > /etc/yum.repos.d/{repo_name}.repo &&
        {' && '.join(commands)} &&
        createrepo /mirror/
        '''
        
        return ['sh', '-c', full_command]
    
    def _generate_yum_repo_config(self, version: str) -> str:
        repo_name = f"{self.dist_config.name}-{version}"
        config_sections = []
        
        for arch in self.dist_config.architectures:
            for mirror_url in self.dist_config.mirror_urls:
                # BaseOS repository
                baseos_config = f"""[{repo_name}-baseos-{arch}]
name={self.dist_config.name.title()} {version} - BaseOS ({arch})
baseurl={mirror_url}{version}/BaseOS/{arch}/os/
enabled=1
gpgcheck=1
"""
                config_sections.append(baseos_config)
                
                # AppStream repository
                appstream_config = f"""[{repo_name}-appstream-{arch}]
name={self.dist_config.name.title()} {version} - AppStream ({arch})
baseurl={mirror_url}{version}/AppStream/{arch}/os/
enabled=1
gpgcheck=1
"""
                config_sections.append(appstream_config)
        
        return "\\n".join(config_sections)
    
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