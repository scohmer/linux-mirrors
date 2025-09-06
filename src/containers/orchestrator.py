#!/usr/bin/env python3

import docker
import logging
import asyncio
from typing import Dict, List, Optional, Any
from pathlib import Path
from config.manager import ConfigManager, DistributionConfig

logger = logging.getLogger(__name__)

class ContainerOrchestrator:
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.config = config_manager.get_config()
        self._client: Optional[docker.DockerClient] = None
        self._running_containers: Dict[str, Any] = {}
    
    @property
    def client(self) -> docker.DockerClient:
        if self._client is None:
            try:
                self._client = docker.from_env()
            except Exception as e:
                raise RuntimeError(f"Failed to connect to Docker: {e}")
        return self._client
    
    def _get_container_name(self, dist_name: str, version: str) -> str:
        return f"linux-mirror-{dist_name}-{version}"
    
    def _get_image_name(self, dist_config: DistributionConfig) -> str:
        if dist_config.type == "apt":
            return "ubuntu:latest"  # Base image for APT-based syncing
        else:  # yum
            return "rockylinux:latest"  # Base image for YUM-based syncing
    
    def _create_dockerfile_content(self, dist_config: DistributionConfig) -> str:
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
        image_tag = f"linux-mirror-{dist_config.name}:latest"
        
        try:
            # Check if image already exists
            self.client.images.get(image_tag)
            logger.info(f"Image {image_tag} already exists")
            return image_tag
        except docker.errors.ImageNotFound:
            pass
        
        dockerfile_content = self._create_dockerfile_content(dist_config)
        
        # Build image from Dockerfile content
        logger.info(f"Building image {image_tag}")
        image, build_logs = self.client.images.build(
            fileobj=dockerfile_content.encode(),
            tag=image_tag,
            rm=True
        )
        
        for log in build_logs:
            if 'stream' in log:
                logger.debug(log['stream'].strip())
        
        logger.info(f"Successfully built image {image_tag}")
        return image_tag
    
    def create_sync_container(self, dist_name: str, version: str, command: List[str]) -> str:
        dist_config = self.config.distributions.get(dist_name)
        if not dist_config:
            raise ValueError(f"Unknown distribution: {dist_name}")
        
        container_name = self._get_container_name(dist_name, version)
        image_tag = self.build_container_image(dist_config)
        
        # Prepare volume mounts
        mirror_path = self.config_manager.get_distribution_path(dist_name)
        volumes = {
            mirror_path: {'bind': '/mirror', 'mode': 'rw'}
        }
        
        # Environment variables
        environment = {
            'DIST_NAME': dist_name,
            'DIST_VERSION': version,
            'MIRROR_PATH': '/mirror'
        }
        
        try:
            # Remove existing container if it exists
            try:
                existing_container = self.client.containers.get(container_name)
                existing_container.remove(force=True)
                logger.info(f"Removed existing container {container_name}")
            except docker.errors.NotFound:
                pass
            
            # Create new container
            container = self.client.containers.create(
                image=image_tag,
                name=container_name,
                command=command,
                volumes=volumes,
                environment=environment,
                detach=True,
                auto_remove=False  # Keep container for log inspection
            )
            
            logger.info(f"Created container {container_name}")
            return container.id
            
        except Exception as e:
            logger.error(f"Failed to create container {container_name}: {e}")
            raise
    
    def start_sync_container(self, container_id: str) -> None:
        try:
            container = self.client.containers.get(container_id)
            container.start()
            
            self._running_containers[container_id] = container
            logger.info(f"Started container {container.name}")
            
        except Exception as e:
            logger.error(f"Failed to start container {container_id}: {e}")
            raise
    
    def stop_container(self, container_id: str, timeout: int = 10) -> None:
        try:
            container = self.client.containers.get(container_id)
            container.stop(timeout=timeout)
            
            if container_id in self._running_containers:
                del self._running_containers[container_id]
            
            logger.info(f"Stopped container {container.name}")
            
        except Exception as e:
            logger.error(f"Failed to stop container {container_id}: {e}")
            raise
    
    def get_container_status(self, container_id: str) -> Dict[str, Any]:
        try:
            container = self.client.containers.get(container_id)
            container.reload()
            
            return {
                'id': container.id[:12],
                'name': container.name,
                'status': container.status,
                'image': container.image.tags[0] if container.image.tags else 'unknown',
                'created': container.attrs['Created'],
                'started': container.attrs.get('State', {}).get('StartedAt'),
                'finished': container.attrs.get('State', {}).get('FinishedAt')
            }
            
        except Exception as e:
            logger.error(f"Failed to get status for container {container_id}: {e}")
            return {'error': str(e)}
    
    def get_container_logs(self, container_id: str, tail: int = 100) -> str:
        try:
            container = self.client.containers.get(container_id)
            logs = container.logs(tail=tail, timestamps=True)
            return logs.decode('utf-8')
            
        except Exception as e:
            logger.error(f"Failed to get logs for container {container_id}: {e}")
            return f"Error retrieving logs: {e}"
    
    def list_running_containers(self) -> List[Dict[str, Any]]:
        try:
            containers = self.client.containers.list(
                filters={'name': 'linux-mirror-*'}
            )
            
            return [
                {
                    'id': c.id[:12],
                    'name': c.name,
                    'status': c.status,
                    'image': c.image.tags[0] if c.image.tags else 'unknown'
                }
                for c in containers
            ]
            
        except Exception as e:
            logger.error(f"Failed to list containers: {e}")
            return []
    
    def cleanup_stopped_containers(self) -> int:
        try:
            containers = self.client.containers.list(
                all=True,
                filters={'name': 'linux-mirror-*', 'status': 'exited'}
            )
            
            count = 0
            for container in containers:
                container.remove()
                count += 1
                logger.info(f"Removed stopped container {container.name}")
            
            return count
            
        except Exception as e:
            logger.error(f"Failed to cleanup containers: {e}")
            return 0