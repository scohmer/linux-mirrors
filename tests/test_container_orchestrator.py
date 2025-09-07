#!/usr/bin/env python3

import os
import json
import pytest
import subprocess
import tempfile
from unittest.mock import Mock, patch, call, MagicMock
from pathlib import Path

from src.config.manager import ConfigManager, DistributionConfig, MirrorConfig
from src.containers.orchestrator import ContainerOrchestrator


class TestContainerOrchestrator:
    """Test ContainerOrchestrator functionality"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.yaml")
        
        # Create mock config manager
        self.mock_config_manager = Mock(spec=ConfigManager)
        self.mock_config = MirrorConfig(
            base_path=self.temp_dir,
            container_runtime="podman"
        )
        self.mock_config_manager.get_config.return_value = self.mock_config
        
        # Mock distribution configs
        self.apt_dist = DistributionConfig(
            name="debian",
            type="apt",
            versions=["bookworm"],
            mirror_urls=["http://deb.debian.org/debian/"],
            components=["main"],
            architectures=["amd64"]
        )
        
        self.yum_dist = DistributionConfig(
            name="rocky",
            type="yum",
            versions=["9"],
            mirror_urls=["https://download.rockylinux.org/pub/rocky/"],
            architectures=["x86_64"]
        )
        
        self.mock_config.distributions = {
            "debian": self.apt_dist,
            "rocky": self.yum_dist
        }
    
    def teardown_method(self):
        """Clean up test fixtures"""
        if os.path.exists(self.temp_dir):
            import shutil
            shutil.rmtree(self.temp_dir)
    
    @patch('subprocess.run')
    def test_init_validates_runtime(self, mock_subprocess):
        """Test that initialization validates container runtime availability"""
        mock_subprocess.return_value = Mock(stdout="podman version 4.0.0", returncode=0)
        
        orchestrator = ContainerOrchestrator(self.mock_config_manager)
        
        mock_subprocess.assert_called_once_with(
            ['podman', '--version'],
            capture_output=True,
            text=True,
            check=True
        )
        assert orchestrator.container_runtime == "podman"
    
    @patch('subprocess.run')
    def test_init_runtime_not_available(self, mock_subprocess):
        """Test initialization fails when container runtime is not available"""
        mock_subprocess.side_effect = FileNotFoundError("podman not found")
        
        with pytest.raises(RuntimeError, match="Container runtime 'podman' not available"):
            ContainerOrchestrator(self.mock_config_manager)
    
    def test_get_container_name(self):
        """Test container name generation"""
        with patch('subprocess.run', return_value=Mock(stdout="podman version 4.0.0")):
            orchestrator = ContainerOrchestrator(self.mock_config_manager)
            
            name = orchestrator._get_container_name("debian", "bookworm")
            assert name == "linux-mirror-debian-bookworm"
    
    def test_get_image_name_apt(self):
        """Test image name selection for APT distributions"""
        with patch('subprocess.run', return_value=Mock(stdout="podman version 4.0.0")):
            orchestrator = ContainerOrchestrator(self.mock_config_manager)
            
            image = orchestrator._get_image_name(self.apt_dist)
            assert image == "docker.io/library/ubuntu:latest"
    
    def test_get_image_name_yum(self):
        """Test image name selection for YUM distributions"""
        with patch('subprocess.run', return_value=Mock(stdout="podman version 4.0.0")):
            orchestrator = ContainerOrchestrator(self.mock_config_manager)
            
            image = orchestrator._get_image_name(self.yum_dist)
            assert image == "docker.io/library/rockylinux:latest"
    
    def test_create_containerfile_content_apt(self):
        """Test Containerfile content generation for APT"""
        with patch('subprocess.run', return_value=Mock(stdout="podman version 4.0.0")):
            orchestrator = ContainerOrchestrator(self.mock_config_manager)
            
            content = orchestrator._create_containerfile_content(self.apt_dist)
            
            assert "FROM ubuntu:latest" in content
            assert "apt-mirror" in content
            assert "debmirror" in content
            assert "WORKDIR /mirror" in content
            assert "VOLUME [\"/mirror\"]" in content
    
    def test_create_containerfile_content_yum(self):
        """Test Containerfile content generation for YUM"""
        with patch('subprocess.run', return_value=Mock(stdout="podman version 4.0.0")):
            orchestrator = ContainerOrchestrator(self.mock_config_manager)
            
            content = orchestrator._create_containerfile_content(self.yum_dist)
            
            assert "FROM rockylinux:latest" in content
            assert "dnf-utils" in content
            assert "createrepo" in content
            assert "WORKDIR /mirror" in content
            assert "VOLUME [\"/mirror\"]" in content
    
    @patch('subprocess.run')
    @patch('tempfile.NamedTemporaryFile')
    @patch('builtins.open', new_callable=MagicMock)
    @patch('os.devnull', '/dev/null')
    def test_build_container_image_success(self, mock_open, mock_tempfile, mock_subprocess):
        """Test successful container image building"""
        # Mock the tempfile
        mock_file = Mock()
        mock_file.name = "/tmp/test.containerfile"
        mock_tempfile.return_value.__enter__.return_value = mock_file
        
        # Mock subprocess calls
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # version check
            Mock(returncode=1),  # image exists check (doesn't exist)
            Mock(returncode=0)   # build command
        ]
        
        orchestrator = ContainerOrchestrator(self.mock_config_manager)
        image_tag = orchestrator.build_container_image(self.apt_dist)
        
        assert image_tag == "localhost/linux-mirror-debian:latest"
        
        # Verify build command was called
        build_calls = [call for call in mock_subprocess.call_args_list if 'build' in str(call)]
        assert len(build_calls) == 1
    
    @patch('subprocess.run')
    def test_build_container_image_already_exists(self, mock_subprocess):
        """Test building when image already exists"""
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # version check
            Mock(returncode=0)  # image exists check (exists)
        ]
        
        orchestrator = ContainerOrchestrator(self.mock_config_manager)
        image_tag = orchestrator.build_container_image(self.apt_dist)
        
        assert image_tag == "localhost/linux-mirror-debian:latest"
        
        # Should only check version and existence, not build
        assert len(mock_subprocess.call_args_list) == 2
    
    @patch('subprocess.run')
    @patch('tempfile.NamedTemporaryFile')
    @patch('builtins.open', new_callable=MagicMock)
    def test_build_container_image_failure(self, mock_open, mock_tempfile, mock_subprocess):
        """Test container image build failure"""
        mock_file = Mock()
        mock_file.name = "/tmp/test.containerfile"
        mock_tempfile.return_value.__enter__.return_value = mock_file
        
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # version check
            Mock(returncode=1),  # image exists check
            subprocess.CalledProcessError(1, "build", stderr="Build failed")  # build failure
        ]
        
        orchestrator = ContainerOrchestrator(self.mock_config_manager)
        
        with pytest.raises(subprocess.CalledProcessError):
            orchestrator.build_container_image(self.apt_dist)
    
    @patch('subprocess.run')
    @patch('os.makedirs')
    def test_create_sync_container_success(self, mock_makedirs, mock_subprocess):
        """Test successful container creation"""
        # Mock the path
        self.mock_config_manager.get_distribution_path.return_value = "/tmp/debian"
        
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # version check
            Mock(returncode=0),  # image exists check
            Mock(returncode=0),  # remove existing container
            Mock(stdout="container123\n", returncode=0)  # create container
        ]
        
        orchestrator = ContainerOrchestrator(self.mock_config_manager)
        
        container_id = orchestrator.create_sync_container(
            "debian", "bookworm", ["apt-mirror", "/mirror/apt-mirror.list"]
        )
        
        assert container_id == "container123"
        
        # Verify create command was called with correct parameters
        create_calls = [call for call in mock_subprocess.call_args_list if 'create' in str(call)]
        assert len(create_calls) == 1
        
        create_args = create_calls[0][0][0]  # First positional argument (command list)
        assert 'create' in create_args
        assert '--name' in create_args
        assert 'linux-mirror-debian-bookworm' in create_args
        assert '--volume' in create_args
    
    @patch('subprocess.run')
    def test_create_sync_container_unknown_distribution(self, mock_subprocess):
        """Test container creation with unknown distribution"""
        mock_subprocess.return_value = Mock(stdout="podman version 4.0.0", returncode=0)
        
        orchestrator = ContainerOrchestrator(self.mock_config_manager)
        
        with pytest.raises(ValueError, match="Unknown distribution: nonexistent"):
            orchestrator.create_sync_container("nonexistent", "version", ["command"])
    
    @patch('subprocess.run')
    def test_start_sync_container_success(self, mock_subprocess):
        """Test successful container start"""
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # version check
            Mock(returncode=0)  # start command
        ]
        
        orchestrator = ContainerOrchestrator(self.mock_config_manager)
        orchestrator.start_sync_container("container123")
        
        # Verify start command was called
        start_calls = [call for call in mock_subprocess.call_args_list if 'start' in str(call)]
        assert len(start_calls) == 1
        assert 'container123' in start_calls[0][0][0]
        
        # Verify container is tracked
        assert "container123" in orchestrator._running_containers
    
    @patch('subprocess.run')
    def test_start_sync_container_failure(self, mock_subprocess):
        """Test container start failure"""
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # version check
            subprocess.CalledProcessError(1, "start", stderr="Start failed")
        ]
        
        orchestrator = ContainerOrchestrator(self.mock_config_manager)
        
        with pytest.raises(RuntimeError, match="Container start failed"):
            orchestrator.start_sync_container("container123")
    
    @patch('subprocess.run')
    def test_stop_container_success(self, mock_subprocess):
        """Test successful container stop"""
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # version check
            Mock(returncode=0)  # stop command
        ]
        
        orchestrator = ContainerOrchestrator(self.mock_config_manager)
        orchestrator._running_containers["container123"] = {"id": "container123"}
        
        orchestrator.stop_container("container123", timeout=5)
        
        # Verify stop command was called with correct timeout
        stop_calls = [call for call in mock_subprocess.call_args_list if 'stop' in str(call)]
        assert len(stop_calls) == 1
        
        stop_args = stop_calls[0][0][0]
        assert 'stop' in stop_args
        assert '-t' in stop_args
        assert '5' in stop_args
        assert 'container123' in stop_args
        
        # Verify container is no longer tracked
        assert "container123" not in orchestrator._running_containers
    
    @patch('subprocess.run')
    def test_get_container_status_success(self, mock_subprocess):
        """Test successful container status retrieval"""
        inspect_data = [{
            'Id': 'container123456789',
            'Name': '/linux-mirror-debian-bookworm',
            'State': {
                'Status': 'running',
                'StartedAt': '2023-01-01T00:00:00Z',
                'FinishedAt': None
            },
            'Config': {'Image': 'localhost/linux-mirror-debian:latest'},
            'Created': '2023-01-01T00:00:00Z'
        }]
        
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # version check
            Mock(stdout=json.dumps(inspect_data), returncode=0)  # inspect command
        ]
        
        orchestrator = ContainerOrchestrator(self.mock_config_manager)
        status = orchestrator.get_container_status("container123")
        
        assert status['id'] == 'container12345'  # First 12 chars
        assert status['name'] == 'linux-mirror-debian-bookworm'
        assert status['status'] == 'running'
        assert status['image'] == 'localhost/linux-mirror-debian:latest'
        assert status['started'] == '2023-01-01T00:00:00Z'
    
    @patch('subprocess.run')
    def test_get_container_status_not_found(self, mock_subprocess):
        """Test container status when container not found"""
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # version check
            subprocess.CalledProcessError(125, "inspect", stderr="No such container")
        ]
        
        orchestrator = ContainerOrchestrator(self.mock_config_manager)
        status = orchestrator.get_container_status("nonexistent")
        
        assert status['status'] == 'not found'
        assert 'error' in status
        assert status['error'] == 'Container not found'
    
    @patch('subprocess.run')
    def test_get_container_logs_success(self, mock_subprocess):
        """Test successful log retrieval"""
        mock_logs = "2023-01-01T00:00:00Z Starting sync...\n2023-01-01T00:01:00Z Sync complete"
        
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # version check
            Mock(stdout=mock_logs, returncode=0)  # logs command
        ]
        
        orchestrator = ContainerOrchestrator(self.mock_config_manager)
        logs = orchestrator.get_container_logs("container123", tail=50)
        
        assert logs == mock_logs
        
        # Verify logs command was called with correct parameters
        logs_calls = [call for call in mock_subprocess.call_args_list if 'logs' in str(call)]
        assert len(logs_calls) == 1
        
        logs_args = logs_calls[0][0][0]
        assert 'logs' in logs_args
        assert '--timestamps' in logs_args
        assert '--tail' in logs_args
        assert '50' in logs_args
        assert 'container123' in logs_args
    
    @patch('subprocess.run')
    def test_list_running_containers_success(self, mock_subprocess):
        """Test successful container listing"""
        containers_data = [
            {
                'Id': 'container123',
                'Names': ['linux-mirror-debian-bookworm'],
                'State': 'running',
                'Image': 'localhost/linux-mirror-debian:latest'
            },
            {
                'Id': 'container456',
                'Names': ['linux-mirror-ubuntu-focal'],
                'State': 'exited',
                'Image': 'localhost/linux-mirror-ubuntu:latest'
            }
        ]
        
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # version check
            Mock(stdout=json.dumps(containers_data), returncode=0)  # ps command
        ]
        
        orchestrator = ContainerOrchestrator(self.mock_config_manager)
        containers = orchestrator.list_running_containers()
        
        assert len(containers) == 2
        
        assert containers[0]['id'] == 'container123'
        assert containers[0]['name'] == 'linux-mirror-debian-bookworm'
        assert containers[0]['status'] == 'running'
        
        assert containers[1]['id'] == 'container456'
        assert containers[1]['name'] == 'linux-mirror-ubuntu-focal'
        assert containers[1]['status'] == 'exited'
    
    @patch('subprocess.run')
    def test_list_running_containers_empty(self, mock_subprocess):
        """Test container listing when no containers exist"""
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # version check
            Mock(stdout="[]", returncode=0)  # ps command (empty)
        ]
        
        orchestrator = ContainerOrchestrator(self.mock_config_manager)
        containers = orchestrator.list_running_containers()
        
        assert containers == []
    
    @patch('subprocess.run')
    def test_cleanup_stopped_containers_success(self, mock_subprocess):
        """Test successful cleanup of stopped containers"""
        stopped_containers = [
            {
                'Id': 'container123',
                'Names': ['linux-mirror-debian-bookworm']
            },
            {
                'Id': 'container456',
                'Names': ['linux-mirror-ubuntu-focal']
            }
        ]
        
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # version check
            Mock(stdout=json.dumps(stopped_containers), returncode=0),  # ps stopped
            Mock(returncode=0),  # rm container123
            Mock(returncode=0),  # rm container456
        ]
        
        orchestrator = ContainerOrchestrator(self.mock_config_manager)
        count = orchestrator.cleanup_stopped_containers()
        
        assert count == 2
        
        # Verify rm commands were called
        rm_calls = [call for call in mock_subprocess.call_args_list if 'rm' in str(call)]
        assert len(rm_calls) == 2
    
    @patch('subprocess.run')
    def test_cleanup_stopped_containers_partial_failure(self, mock_subprocess):
        """Test cleanup with some containers failing to remove"""
        stopped_containers = [
            {'Id': 'container123', 'Names': ['container1']},
            {'Id': 'container456', 'Names': ['container2']}
        ]
        
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # version check
            Mock(stdout=json.dumps(stopped_containers), returncode=0),  # ps stopped
            Mock(returncode=0),  # rm container123 (success)
            subprocess.CalledProcessError(1, "rm", stderr="Failed to remove")  # rm container456 (fail)
        ]
        
        orchestrator = ContainerOrchestrator(self.mock_config_manager)
        count = orchestrator.cleanup_stopped_containers()
        
        assert count == 1  # Only one successfully removed


@pytest.mark.integration 
@pytest.mark.container
class TestContainerOrchestratorIntegration:
    """Integration tests requiring actual container runtime"""
    
    def test_runtime_detection(self):
        """Test detection of available container runtime"""
        try:
            # This will use actual container runtime if available
            config_manager = Mock()
            config_manager.get_config.return_value = MirrorConfig(container_runtime="podman")
            
            orchestrator = ContainerOrchestrator(config_manager)
            assert orchestrator.container_runtime in ["podman", "docker"]
        except RuntimeError:
            pytest.skip("No container runtime available")


@pytest.mark.slow
class TestContainerOrchestratorStressTests:
    """Stress tests for container orchestrator"""
    
    def test_multiple_container_operations(self):
        """Test handling multiple container operations"""
        # This would test creating/managing many containers simultaneously
        # Implementation depends on actual container runtime availability
        pytest.skip("Stress tests require container runtime")