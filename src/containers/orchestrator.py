#!/usr/bin/env python3

import logging
import asyncio
import subprocess
import json
from typing import Dict, List, Optional, Any
from pathlib import Path
from config.manager import ConfigManager, DistributionConfig

logger = logging.getLogger(__name__)

class ContainerOrchestrator:
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.config = config_manager.get_config()
        self.container_runtime = self.config.container_runtime
        self._running_containers: Dict[str, Any] = {}
        
        # Validate container runtime is available
        self._validate_runtime()
    
    def _validate_runtime(self):
        """Validate that the specified container runtime is available"""
        try:
            result = subprocess.run([self.container_runtime, '--version'], 
                                  capture_output=True, text=True, check=True)
            logger.info(f"Using {self.container_runtime}: {result.stdout.strip()}")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise RuntimeError(f"Container runtime '{self.container_runtime}' not available: {e}")
    
    def _get_container_name(self, dist_name: str, version: str) -> str:
        return f"linux-mirror-{dist_name}-{version}"
    
    def _get_image_name(self, dist_config: DistributionConfig) -> str:
        if dist_config.type == "apt":
            return "docker.io/library/ubuntu:latest"  # Base image for APT-based syncing
        else:  # yum
            return "docker.io/library/rockylinux:latest"  # Base image for YUM-based syncing
    
    def _create_containerfile_content(self, dist_config: DistributionConfig) -> str:
        if dist_config.type == "apt":
            return '''FROM ubuntu:latest
RUN apt-get update && apt-get install -y \\
    apt-mirror \\
    debmirror \\
    wget \\
    curl \\
    rsync \\
    && apt-get clean \\
    && rm -rf /var/lib/apt/lists/*

WORKDIR /mirror
VOLUME ["/mirror"]
'''
        else:  # yum
            return '''FROM rockylinux:latest
RUN dnf install -y \\
    dnf-utils \\
    createrepo \\
    wget \\
    curl \\
    rsync \\
    && dnf clean all

WORKDIR /mirror
VOLUME ["/mirror"]
'''
    
    def build_container_image(self, dist_config: DistributionConfig) -> str:
        image_tag = f"localhost/linux-mirror-{dist_config.name}:latest"
        
        try:
            # Check if image already exists
            result = subprocess.run([self.container_runtime, 'image', 'exists', image_tag], 
                                  capture_output=True, check=False)
            if result.returncode == 0:
                logger.info(f"Image {image_tag} already exists")
                return image_tag
        except Exception as e:
            logger.debug(f"Error checking image existence: {e}")
        
        containerfile_content = self._create_containerfile_content(dist_config)
        
        # Build image using containerfile
        logger.info(f"Building image {image_tag}")
        
        # Write temporary Containerfile
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.containerfile', delete=False) as f:
            f.write(containerfile_content)
            containerfile_path = f.name
        
        try:
            result = subprocess.run([
                self.container_runtime, 'build', 
                '-t', image_tag,
                '-f', containerfile_path,
                '.'  # build context
            ], capture_output=True, text=True, check=True)
            
            logger.debug(f"Build output: {result.stdout}")
            logger.info(f"Successfully built image {image_tag}")
            return image_tag
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to build image {image_tag}: {e.stderr}")
            raise
        finally:
            # Clean up temporary file
            Path(containerfile_path).unlink(missing_ok=True)
    
    def create_sync_container(self, dist_name: str, version: str, command: List[str]) -> str:
        dist_config = self.config.distributions.get(dist_name)
        if not dist_config:
            raise ValueError(f"Unknown distribution: {dist_name}")
        
        container_name = self._get_container_name(dist_name, version)
        image_tag = self.build_container_image(dist_config)
        
        # Prepare volume mounts
        mirror_path = self.config_manager.get_distribution_path(dist_name)
        
        try:
            # Remove existing container if it exists
            try:
                subprocess.run([self.container_runtime, 'rm', '-f', container_name], 
                             capture_output=True, check=True)
                logger.info(f"Removed existing container {container_name}")
            except subprocess.CalledProcessError:
                pass  # Container doesn't exist
            
            # Create new container
            create_cmd = [
                self.container_runtime, 'create',
                '--name', container_name,
                '--volume', f"{mirror_path}:/mirror:rw",
                '--env', f'DIST_NAME={dist_name}',
                '--env', f'DIST_VERSION={version}',
                '--env', 'MIRROR_PATH=/mirror',
                image_tag
            ] + command
            
            result = subprocess.run(create_cmd, capture_output=True, text=True, check=True)
            container_id = result.stdout.strip()
            
            logger.info(f"Created container {container_name}")
            return container_id
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to create container {container_name}: {e.stderr}")
            raise RuntimeError(f"Container creation failed: {e.stderr}")
    
    def start_sync_container(self, container_id: str) -> None:
        try:
            result = subprocess.run([self.container_runtime, 'start', container_id], 
                                  capture_output=True, text=True, check=True)
            
            self._running_containers[container_id] = {'id': container_id}
            logger.info(f"Started container {container_id}")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to start container {container_id}: {e.stderr}")
            raise RuntimeError(f"Container start failed: {e.stderr}")
    
    def stop_container(self, container_id: str, timeout: int = 10) -> None:
        try:
            subprocess.run([self.container_runtime, 'stop', '-t', str(timeout), container_id], 
                         capture_output=True, text=True, check=True)
            
            if container_id in self._running_containers:
                del self._running_containers[container_id]
            
            logger.info(f"Stopped container {container_id}")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to stop container {container_id}: {e.stderr}")
            raise RuntimeError(f"Container stop failed: {e.stderr}")
    
    def get_container_status(self, container_id: str) -> Dict[str, Any]:
        try:
            result = subprocess.run([self.container_runtime, 'inspect', container_id], 
                                  capture_output=True, text=True, check=True)
            
            inspect_data = json.loads(result.stdout)[0]
            
            return {
                'id': inspect_data['Id'][:12],
                'name': inspect_data['Name'].lstrip('/'),
                'status': inspect_data['State']['Status'],
                'image': inspect_data['Config']['Image'],
                'created': inspect_data['Created'],
                'started': inspect_data['State'].get('StartedAt'),
                'finished': inspect_data['State'].get('FinishedAt')
            }
            
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to get status for container {container_id}: {e}")
            return {'error': str(e)}
    
    def get_container_logs(self, container_id: str, tail: int = 100) -> str:
        try:
            result = subprocess.run([self.container_runtime, 'logs', '--timestamps', '--tail', str(tail), container_id], 
                                  capture_output=True, text=True, check=True)
            return result.stdout
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to get logs for container {container_id}: {e.stderr}")
            return f"Error retrieving logs: {e.stderr}"
    
    def list_running_containers(self) -> List[Dict[str, Any]]:
        try:
            result = subprocess.run([self.container_runtime, 'ps', '-a', '--format', 'json', 
                                   '--filter', 'name=linux-mirror-'], 
                                  capture_output=True, text=True, check=True)
            
            containers = []
            stdout = result.stdout.strip()
            
            if not stdout or stdout == '[]':
                return containers
            
            try:
                # Try to parse as a single JSON array first
                container_list = json.loads(stdout)
                if isinstance(container_list, list):
                    for container_data in container_list:
                        if isinstance(container_data, dict):
                            containers.append({
                                'id': container_data.get('Id', 'unknown')[:12],
                                'name': container_data.get('Names', ['unknown'])[0] if isinstance(container_data.get('Names'), list) else container_data.get('Names', 'unknown'),
                                'status': container_data.get('State', 'unknown'),
                                'image': container_data.get('Image', 'unknown')
                            })
                else:
                    # Single container object
                    containers.append({
                        'id': container_list.get('Id', 'unknown')[:12],
                        'name': container_list.get('Names', ['unknown'])[0] if isinstance(container_list.get('Names'), list) else container_list.get('Names', 'unknown'),
                        'status': container_list.get('State', 'unknown'),
                        'image': container_list.get('Image', 'unknown')
                    })
            except json.JSONDecodeError:
                # Fallback: try parsing line by line (Docker-style output)
                for line in stdout.split('\n'):
                    if line.strip():
                        try:
                            container_data = json.loads(line)
                            containers.append({
                                'id': container_data.get('Id', 'unknown')[:12],
                                'name': container_data.get('Names', ['unknown'])[0] if isinstance(container_data.get('Names'), list) else container_data.get('Names', 'unknown'),
                                'status': container_data.get('State', 'unknown'),
                                'image': container_data.get('Image', 'unknown')
                            })
                        except (json.JSONDecodeError, KeyError) as e:
                            logger.debug(f"Error parsing container data: {e}")
            
            return containers
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to list containers: {e.stderr}")
            return []
    
    def cleanup_stopped_containers(self) -> int:
        try:
            # Get all stopped containers with our naming pattern
            result = subprocess.run([self.container_runtime, 'ps', '-a', '--format', 'json',
                                   '--filter', 'name=linux-mirror-',
                                   '--filter', 'status=exited'], 
                                  capture_output=True, text=True, check=True)
            
            count = 0
            for line in result.stdout.strip().split('\n'):
                if line:
                    try:
                        container_data = json.loads(line)
                        container_id = container_data['Id']
                        container_name = container_data['Names']
                        
                        subprocess.run([self.container_runtime, 'rm', container_id], 
                                     capture_output=True, check=True)
                        count += 1
                        logger.info(f"Removed stopped container {container_name}")
                        
                    except (json.JSONDecodeError, subprocess.CalledProcessError) as e:
                        logger.debug(f"Error removing container: {e}")
            
            return count
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to cleanup containers: {e.stderr}")
            return 0