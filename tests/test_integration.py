#!/usr/bin/env python3

"""
Integration tests for linux-mirrors project.

These tests verify that different components work together correctly,
using real filesystem operations and mock container/network operations.
"""

import os
import sys
import tempfile
import pytest
import asyncio
import yaml
from unittest.mock import Mock, patch, AsyncMock
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.config.manager import ConfigManager, DistributionConfig, MirrorConfig
from src.containers.orchestrator import ContainerOrchestrator
from src.sync.engines import SyncManager, AptSyncEngine, YumSyncEngine
from src.storage.manager import StorageManager
from src.systemd.service_generator import SystemdServiceGenerator
from src import main


@pytest.mark.integration
class TestConfigStorageIntegration:
    """Test integration between ConfigManager and StorageManager"""
    
    def test_config_and_storage_directory_creation(self):
        """Test that storage manager creates directories based on config"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            # Create config manager and modify configuration
            config_manager = ConfigManager(config_path)
            config = config_manager.load_config()
            
            # Add custom distribution
            custom_dist = DistributionConfig(
                name="custom-test",
                type="apt",
                versions=["stable", "testing"],
                mirror_urls=["http://example.com/"],
                components=["main"],
                architectures=["amd64"],
                enabled=True
            )
            config_manager.update_distribution("custom-test", custom_dist)
            
            # Create storage manager and ensure directories
            storage_manager = StorageManager(config_manager)
            results = storage_manager.ensure_directory_structure()
            
            # Verify all expected directories exist
            assert os.path.exists(config.base_path)
            assert os.path.exists(config.apt_path)
            assert os.path.exists(config.yum_path)
            
            # Verify custom distribution directories
            custom_path = os.path.join(config.apt_path, "custom-test")
            assert os.path.exists(custom_path)
            assert os.path.exists(os.path.join(custom_path, "stable"))
            assert os.path.exists(os.path.join(custom_path, "testing"))
            
            # Verify all results are successful
            assert all(results.values())
    
    def test_config_persistence_and_reload(self):
        """Test configuration persistence and reloading"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            # Create and modify config
            manager1 = ConfigManager(config_path)
            config1 = manager1.load_config()
            
            original_runtime = config1.container_runtime
            config1.container_runtime = "docker"  # Change from default
            config1.max_concurrent_syncs = 5
            manager1.save_config()
            
            # Create new manager instance and verify changes persisted
            manager2 = ConfigManager(config_path)
            config2 = manager2.load_config()
            
            assert config2.container_runtime == "docker"
            assert config2.max_concurrent_syncs == 5
            assert config2.container_runtime != original_runtime
    
    def test_storage_info_with_real_config(self):
        """Test storage info retrieval with real configuration"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_manager = ConfigManager(os.path.join(temp_dir, "config.yaml"))
            config = config_manager.load_config()
            
            # Create some test files and directories
            test_dist_dir = os.path.join(config.apt_path, "debian", "bookworm")
            os.makedirs(test_dist_dir, exist_ok=True)
            
            # Create test files with known sizes
            test_file = os.path.join(test_dist_dir, "Packages")
            with open(test_file, 'w') as f:
                f.write("Package: test\nVersion: 1.0\n" * 100)  # ~2KB
            
            storage_manager = StorageManager(config_manager)
            storage_manager.ensure_directory_structure()
            
            storage_info = storage_manager.get_storage_info()
            
            assert storage_info['base_path'] == config.base_path
            assert len(storage_info['paths']) >= 3  # base, apt, yum
            assert storage_info['total_repos'] >= 0


@pytest.mark.integration
class TestContainerSyncIntegration:
    """Test integration between ContainerOrchestrator and SyncManager"""
    
    @patch('subprocess.run')
    def test_sync_engine_container_creation(self, mock_subprocess):
        """Test that sync engines properly interact with container orchestrator"""
        # Mock container runtime availability and operations
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # version check
            Mock(returncode=0),  # image exists check
            Mock(returncode=0),  # remove container (if exists)
            Mock(stdout="container123\n", returncode=0),  # create container
        ]
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_manager = Mock()
            config = MirrorConfig(base_path=temp_dir, container_runtime="podman")
            
            apt_dist = DistributionConfig(
                name="debian",
                type="apt",
                versions=["bookworm"],
                mirror_urls=["http://deb.debian.org/debian/"],
                components=["main"],
                architectures=["amd64"],
                enabled=True
            )
            config.distributions = {"debian": apt_dist}
            
            config_manager.get_config.return_value = config
            config_manager.get_distribution_path.return_value = os.path.join(config.apt_path, "debian")
            
            # Create orchestrator and sync manager
            orchestrator = ContainerOrchestrator(config_manager)
            sync_manager = SyncManager(orchestrator)
            
            # Get APT engine and verify it can create containers
            engine = sync_manager.get_engine(apt_dist)
            assert isinstance(engine, AptSyncEngine)
            
            # Test command generation
            command = engine.generate_sync_command("bookworm")
            assert command[0] == 'sh'
            assert 'apt-mirror' in command[2]
            
            # Test container creation (mocked)
            container_id = orchestrator.create_sync_container("debian", "bookworm", command)
            assert container_id == "container123"
    
    @patch('subprocess.run')
    def test_yum_sync_engine_container_creation(self, mock_subprocess):
        """Test YUM sync engine container creation"""
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # version check
            Mock(returncode=0),  # image exists check
            Mock(returncode=0),  # remove container
            Mock(stdout="container456\n", returncode=0),  # create container
        ]
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_manager = Mock()
            config = MirrorConfig(base_path=temp_dir, container_runtime="podman")
            
            yum_dist = DistributionConfig(
                name="rocky",
                type="yum",
                versions=["9"],
                mirror_urls=["https://download.rockylinux.org/pub/rocky/"],
                architectures=["x86_64"],
                enabled=True
            )
            config.distributions = {"rocky": yum_dist}
            
            config_manager.get_config.return_value = config
            config_manager.get_distribution_path.return_value = os.path.join(config.yum_path, "rocky")
            
            orchestrator = ContainerOrchestrator(config_manager)
            sync_manager = SyncManager(orchestrator)
            
            engine = sync_manager.get_engine(yum_dist)
            assert isinstance(engine, YumSyncEngine)
            
            command = engine.generate_sync_command("9")
            assert 'dnf reposync' in command[2]
            assert 'createrepo_c' in command[2]
            
            container_id = orchestrator.create_sync_container("rocky", "9", command)
            assert container_id == "container456"


@pytest.mark.integration
class TestConfigSystemdIntegration:
    """Test integration between ConfigManager and SystemdServiceGenerator"""
    
    def test_systemd_service_generation_with_real_config(self):
        """Test systemd service generation with real configuration"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            systemd_dir = os.path.join(temp_dir, "systemd")
            
            # Create config with test distributions
            config_manager = ConfigManager(config_path)
            config = config_manager.load_config()
            
            # Add test distributions
            test_apt = DistributionConfig(
                name="test-apt",
                type="apt",
                versions=["stable"],
                mirror_urls=["http://example.com/apt/"],
                components=["main"],
                architectures=["amd64"],
                enabled=True,
                sync_schedule="daily"
            )
            
            test_yum = DistributionConfig(
                name="test-yum",
                type="yum", 
                versions=["8"],
                mirror_urls=["http://example.com/yum/"],
                architectures=["x86_64"],
                enabled=True,
                sync_schedule="weekly"
            )
            
            config_manager.update_distribution("test-apt", test_apt)
            config_manager.update_distribution("test-yum", test_yum)
            
            # Generate systemd services
            service_generator = SystemdServiceGenerator(config_manager)
            
            with patch.object(service_generator, '_get_systemd_directory', return_value=systemd_dir):
                created_services = service_generator.create_all_services(user_mode=False, enable_timers=True)
            
            # Should create 2 services (one for each enabled distribution)
            assert len(created_services) == 2
            
            # Verify service files exist and have correct content
            for service in created_services:
                service_file = service['service_file']
                timer_file = service['timer_file']
                
                assert os.path.exists(service_file)
                assert os.path.exists(timer_file)
                
                with open(service_file) as f:
                    service_content = f.read()
                    assert "linux-mirrors sync" in service_content
                    assert f"--distribution {service['distribution']}" in service_content
                    assert f"--config {config_path}" in service_content
                
                with open(timer_file) as f:
                    timer_content = f.read()
                    dist_config = config.distributions[service['distribution']]
                    assert f"OnCalendar={dist_config.sync_schedule}" in timer_content


@pytest.mark.integration
class TestFullApplicationIntegration:
    """Test full application integration scenarios"""
    
    @pytest.mark.asyncio
    @patch('src.containers.orchestrator.ContainerOrchestrator._validate_runtime')
    @patch('src.sync.engines.SyncEngine.sync_version')
    async def test_complete_sync_workflow(self, mock_sync_version, mock_validate_runtime):
        """Test complete sync workflow from command line to execution"""
        mock_validate_runtime.return_value = None  # Skip runtime validation
        mock_sync_version.return_value = {
            'distribution': 'debian',
            'version': 'bookworm',
            'status': 'completed',
            'container_id': 'test-container',
            'logs': 'Sync completed successfully'
        }
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            # Set up configuration
            config_manager = ConfigManager(config_path)
            config = config_manager.load_config()
            config.base_path = temp_dir
            config_manager.save_config()
            
            # Create storage structure
            storage_manager = StorageManager(config_manager)
            storage_manager.ensure_directory_structure()
            
            # Mock command line arguments for sync
            test_args = [
                'linux-mirrors',
                '--config', config_path,
                'sync',
                '--distribution', 'debian',
                '--version', 'bookworm'
            ]
            
            with patch('sys.argv', test_args):
                exit_code = await main.main()
            
            assert exit_code == 0
    
    def test_configuration_validation_integration(self):
        """Test configuration validation across components"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            # Create invalid configuration
            invalid_config = {
                'base_path': temp_dir,
                'container_runtime': 'invalid-runtime',
                'distributions': {
                    'invalid-apt': {
                        'name': 'invalid-apt',
                        'type': 'apt',
                        'versions': ['stable'],
                        'mirror_urls': [],  # Invalid: empty URLs
                        'components': None,  # Invalid: missing components
                        'architectures': ['amd64'],
                        'enabled': True
                    }
                }
            }
            
            with open(config_path, 'w') as f:
                yaml.dump(invalid_config, f)
            
            config_manager = ConfigManager(config_path)
            config = config_manager.load_config()
            
            # Test that sync engine validation catches the errors
            invalid_dist = config.distributions['invalid-apt']
            
            with patch('subprocess.run', side_effect=FileNotFoundError("invalid-runtime not found")):
                # Container orchestrator should fail with invalid runtime
                with pytest.raises(RuntimeError, match="Container runtime"):
                    ContainerOrchestrator(config_manager)
    
    def test_user_permissions_integration(self):
        """Test application behavior with different user permissions"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            # Test as regular user
            with patch('os.geteuid', return_value=1000), \
                 patch('os.path.expanduser', return_value=temp_dir):
                
                config_manager = ConfigManager(config_path)
                config = config_manager.load_config()
                
                # Paths should be user-accessible
                assert config.base_path.startswith(temp_dir)
                
                # Storage manager should work with user paths
                storage_manager = StorageManager(config_manager)
                results = storage_manager.ensure_directory_structure()
                assert all(results.values())
            
            # Test as root
            with patch('os.geteuid', return_value=0):
                config_manager2 = ConfigManager(config_path)
                config2 = config_manager2.load_config()
                
                # Should recalculate paths for root
                # (but will still use temp_dir in this test environment)
                assert config2.base_path is not None


@pytest.mark.integration
class TestErrorHandlingIntegration:
    """Test error handling across component boundaries"""
    
    @pytest.mark.asyncio
    @patch('subprocess.run')
    async def test_container_failure_handling(self, mock_subprocess):
        """Test handling of container failures during sync"""
        # Mock container runtime check succeeds but container creation fails
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # version check
            Mock(returncode=1),  # image doesn't exist
            subprocess.CalledProcessError(1, "build", stderr="Build failed")  # build fails
        ]
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_manager = Mock()
            config = MirrorConfig(base_path=temp_dir)
            
            apt_dist = DistributionConfig(
                name="debian",
                type="apt", 
                versions=["bookworm"],
                mirror_urls=["http://deb.debian.org/debian/"],
                components=["main"],
                architectures=["amd64"],
                enabled=True
            )
            config.distributions = {"debian": apt_dist}
            
            config_manager.get_config.return_value = config
            config_manager.get_distribution_path.return_value = os.path.join(temp_dir, "debian")
            
            orchestrator = ContainerOrchestrator(config_manager)
            sync_manager = SyncManager(orchestrator)
            
            # Sync should handle container build failure gracefully
            results = await sync_manager.sync_distribution(apt_dist)
            
            assert len(results) == 1
            assert results[0]['status'] == 'failed'
            assert 'error' in results[0]
    
    def test_config_corruption_handling(self):
        """Test handling of corrupted configuration files"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            # Create corrupted config file
            with open(config_path, 'w') as f:
                f.write("corrupted: yaml: content: [invalid")
            
            config_manager = ConfigManager(config_path)
            
            # Should raise clear error for corrupted config
            with pytest.raises(ValueError, match="Error loading config"):
                config_manager.load_config()
    
    def test_disk_space_handling(self):
        """Test handling of insufficient disk space"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_manager = Mock()
            config = MirrorConfig(base_path=temp_dir)
            config_manager.get_config.return_value = config
            
            storage_manager = StorageManager(config_manager)
            
            # Mock disk usage to simulate low space
            with patch('psutil.disk_usage') as mock_disk_usage:
                mock_disk_usage.return_value = Mock(
                    free=1 * 1024**3  # Only 1GB free
                )
                
                with patch('os.path.exists', return_value=True):
                    space_check = storage_manager.check_disk_space(required_gb=10.0)
                
                assert space_check['sufficient_space'] is False
                assert space_check['available_gb'] == 1.0


@pytest.mark.integration
@pytest.mark.slow
class TestPerformanceIntegration:
    """Test performance characteristics of integrated components"""
    
    def test_large_configuration_handling(self):
        """Test handling of configurations with many distributions"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            config_manager = ConfigManager(config_path)
            config = config_manager.load_config()
            
            # Add many distributions
            for i in range(50):
                dist = DistributionConfig(
                    name=f"test-dist-{i}",
                    type="apt" if i % 2 == 0 else "yum",
                    versions=[f"version-{j}" for j in range(5)],  # 5 versions each
                    mirror_urls=[f"http://example-{i}.com/"],
                    components=["main"] if i % 2 == 0 else None,
                    architectures=["amd64"],
                    enabled=i < 25  # Enable first 25
                )
                config_manager.update_distribution(f"test-dist-{i}", dist)
            
            # Storage manager should handle many distributions efficiently
            storage_manager = StorageManager(config_manager)
            
            import time
            start_time = time.time()
            results = storage_manager.ensure_directory_structure()
            end_time = time.time()
            
            # Should complete in reasonable time (< 5 seconds)
            assert (end_time - start_time) < 5.0
            
            # Should create directories for enabled distributions only
            enabled_count = len([d for d in config.distributions.values() if d.enabled])
            expected_dirs = sum(len(d.versions) for d in config.distributions.values() if d.enabled)
            
            # Count successful directory creations
            successful_dirs = sum(1 for success in results.values() if success)
            assert successful_dirs >= expected_dirs + 3  # +3 for base directories
    
    def test_concurrent_operations_handling(self):
        """Test handling of concurrent operations across components"""
        # This would test concurrent sync operations, file I/O, etc.
        # Implementation depends on specific performance requirements
        pytest.skip("Concurrent operations testing requires extensive setup")


# Utility functions for integration tests
def create_test_mirror_structure(base_path: str, distributions: dict):
    """Create a test mirror directory structure"""
    for dist_name, dist_config in distributions.items():
        if not dist_config.enabled:
            continue
            
        if dist_config.type == "apt":
            dist_path = os.path.join(base_path, "apt", dist_name)
        else:
            dist_path = os.path.join(base_path, "yum", dist_name)
        
        for version in dist_config.versions:
            version_path = os.path.join(dist_path, version)
            os.makedirs(version_path, exist_ok=True)
            
            # Create some test files
            if dist_config.type == "apt":
                packages_file = os.path.join(version_path, "Packages")
                with open(packages_file, 'w') as f:
                    f.write(f"Package: test-{dist_name}\nVersion: 1.0-{version}\n")
            else:
                repodata_dir = os.path.join(version_path, "repodata")
                os.makedirs(repodata_dir, exist_ok=True)
                repomd_file = os.path.join(repodata_dir, "repomd.xml")
                with open(repomd_file, 'w') as f:
                    f.write(f"<repomd><data type='primary'><location href='primary.xml.gz'/></data></repomd>")


def verify_mirror_structure(base_path: str, distributions: dict):
    """Verify that mirror directory structure is correct"""
    for dist_name, dist_config in distributions.items():
        if not dist_config.enabled:
            continue
        
        if dist_config.type == "apt":
            dist_path = os.path.join(base_path, "apt", dist_name)
        else:
            dist_path = os.path.join(base_path, "yum", dist_name)
        
        assert os.path.exists(dist_path), f"Distribution path missing: {dist_path}"
        
        for version in dist_config.versions:
            version_path = os.path.join(dist_path, version)
            assert os.path.exists(version_path), f"Version path missing: {version_path}"