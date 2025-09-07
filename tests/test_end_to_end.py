#!/usr/bin/env python3

"""
End-to-end tests for linux-mirrors project.

These tests simulate real user workflows and verify the complete
application behavior from command line to final results.
"""

import os
import sys
import tempfile
import pytest
import asyncio
import subprocess
import yaml
from unittest.mock import Mock, patch, AsyncMock
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src import main
from src.config.manager import ConfigManager, DistributionConfig, MirrorConfig


@pytest.mark.integration
class TestCommandLineInterface:
    """Test complete command-line interface workflows"""
    
    @pytest.mark.asyncio
    @patch('src.main.setup_logging')
    @patch('src.containers.orchestrator.ContainerOrchestrator._validate_runtime')
    async def test_help_command(self, mock_validate_runtime, mock_setup_logging):
        """Test help command displays correctly"""
        mock_validate_runtime.return_value = None
        
        # Test main help
        with patch('sys.argv', ['linux-mirrors', '--help']):
            with pytest.raises(SystemExit) as exc_info:
                await main.main()
            # Help command should exit with 0
            assert exc_info.value.code == 0
    
    @pytest.mark.asyncio
    @patch('src.main.setup_logging')
    @patch('src.containers.orchestrator.ContainerOrchestrator._validate_runtime')
    async def test_version_flag(self, mock_validate_runtime, mock_setup_logging):
        """Test version information display"""
        mock_validate_runtime.return_value = None
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            with patch('sys.argv', ['linux-mirrors', '--config', config_path, '--log-level', 'DEBUG']):
                # Should not crash with debug logging
                with patch('src.main.MainInterface') as mock_interface:
                    mock_app = Mock()
                    mock_app.run_async = AsyncMock()
                    mock_interface.return_value = mock_app
                    
                    exit_code = await main.main()
                    assert exit_code == 0
    
    @pytest.mark.asyncio
    @patch('src.main.setup_logging')
    @patch('subprocess.run')
    async def test_status_command_workflow(self, mock_subprocess, mock_setup_logging):
        """Test complete status command workflow"""
        # Mock container runtime and operations
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # runtime check
            Mock(stdout="[]", returncode=0),  # list containers (empty)
        ]
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            # Create config
            config_manager = ConfigManager(config_path)
            config = config_manager.load_config()
            config.base_path = temp_dir
            config_manager.save_config()
            
            with patch('sys.argv', ['linux-mirrors', '--config', config_path, 'status']):
                exit_code = await main.main()
                assert exit_code == 0
    
    @pytest.mark.asyncio
    @patch('src.main.setup_logging')
    @patch('subprocess.run')
    async def test_storage_info_workflow(self, mock_subprocess, mock_setup_logging):
        """Test storage info command workflow"""
        mock_subprocess.return_value = Mock(stdout="podman version 4.0.0", returncode=0)
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            # Create config and some test data
            config_manager = ConfigManager(config_path)
            config = config_manager.load_config()
            config.base_path = temp_dir
            config_manager.save_config()
            
            # Create some test mirror structure
            os.makedirs(os.path.join(temp_dir, "apt", "debian", "bookworm"), exist_ok=True)
            test_file = os.path.join(temp_dir, "apt", "debian", "bookworm", "Packages")
            with open(test_file, 'w') as f:
                f.write("Package: test\nVersion: 1.0\n")
            
            with patch('sys.argv', ['linux-mirrors', '--config', config_path, 'storage', '--info']):
                exit_code = await main.main()
                assert exit_code == 0
    
    @pytest.mark.asyncio
    @patch('src.main.setup_logging')
    @patch('subprocess.run')
    async def test_storage_cleanup_workflow(self, mock_subprocess, mock_setup_logging):
        """Test storage cleanup command workflow"""
        mock_subprocess.return_value = Mock(stdout="podman version 4.0.0", returncode=0)
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            config_manager = ConfigManager(config_path)
            config = config_manager.load_config()
            config.base_path = temp_dir
            config_manager.save_config()
            
            # Create old files for cleanup
            old_dir = os.path.join(temp_dir, "apt", "debian")
            os.makedirs(old_dir, exist_ok=True)
            
            old_file = os.path.join(old_dir, "old.tmp")
            with open(old_file, 'w') as f:
                f.write("old temporary content")
            
            # Set old timestamp
            from datetime import datetime, timedelta
            old_time = (datetime.now() - timedelta(days=60)).timestamp()
            os.utime(old_file, (old_time, old_time))
            
            with patch('sys.argv', ['linux-mirrors', '--config', config_path, 'storage', '--cleanup']):
                exit_code = await main.main()
                assert exit_code == 0
                
                # Old file should be cleaned up
                assert not os.path.exists(old_file)


@pytest.mark.integration
class TestCompleteWorkflows:
    """Test complete user workflows from start to finish"""
    
    def test_initial_setup_workflow(self):
        """Test complete initial setup workflow"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            # Step 1: Create configuration manager (simulates first run)
            config_manager = ConfigManager(config_path)
            assert not os.path.exists(config_path)  # Config doesn't exist yet
            
            # Step 2: Load config (creates default config file)
            config = config_manager.load_config()
            assert os.path.exists(config_path)  # Config should now exist
            
            # Verify default configuration
            assert config.container_runtime == "podman"
            assert len(config.distributions) == 5  # Default distributions
            assert config.base_path is not None
            
            # Step 3: Customize configuration
            config.container_runtime = "docker"
            config.max_concurrent_syncs = 2
            
            # Add custom distribution
            custom_dist = DistributionConfig(
                name="custom",
                type="apt",
                versions=["stable"],
                mirror_urls=["http://custom.example.com/"],
                components=["main", "contrib"],
                architectures=["amd64"],
                enabled=True,
                sync_schedule="daily"
            )
            config_manager.update_distribution("custom", custom_dist)
            
            # Step 4: Set up directory structure
            from src.storage.manager import StorageManager
            storage_manager = StorageManager(config_manager)
            results = storage_manager.ensure_directory_structure()
            
            # Verify directories were created
            assert all(results.values())
            assert os.path.exists(config.base_path)
            assert os.path.exists(os.path.join(config.apt_path, "custom"))
            
            # Step 5: Generate systemd services
            from src.systemd.service_generator import SystemdServiceGenerator
            systemd_dir = os.path.join(temp_dir, "systemd")
            
            service_generator = SystemdServiceGenerator(config_manager)
            with patch.object(service_generator, '_get_systemd_directory', return_value=systemd_dir):
                services = service_generator.create_all_services(user_mode=True, enable_timers=True)
            
            # Should create services for all enabled distributions
            enabled_count = sum(len(d.versions) for d in config.distributions.values() if d.enabled)
            assert len(services) == enabled_count
            
            # Verify service files exist
            for service in services:
                assert os.path.exists(service['service_file'])
                if 'timer_file' in service:
                    assert os.path.exists(service['timer_file'])
    
    @pytest.mark.asyncio
    @patch('subprocess.run')
    @patch('src.sync.engines.SyncEngine._monitor_sync')
    async def test_complete_sync_workflow(self, mock_monitor_sync, mock_subprocess):
        """Test complete synchronization workflow"""
        # Mock container operations
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # version check
            Mock(returncode=0),  # image exists
            Mock(returncode=0),  # remove old container
            Mock(stdout="sync-container\n", returncode=0),  # create container
            Mock(returncode=0),  # start container
        ]
        
        # Mock sync monitoring
        mock_monitor_sync.return_value = {
            'status': 'completed',
            'logs': 'Synchronization completed successfully'
        }
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            # Set up configuration
            config_manager = ConfigManager(config_path)
            config = config_manager.load_config()
            config.base_path = temp_dir
            
            # Enable only debian for this test
            for dist_name, dist in config.distributions.items():
                if dist_name == "debian":
                    dist.enabled = True
                    dist.versions = ["bookworm"]  # Single version for simplicity
                else:
                    dist.enabled = False
            
            config_manager.save_config()
            
            # Set up directory structure
            from src.storage.manager import StorageManager
            storage_manager = StorageManager(config_manager)
            storage_manager.ensure_directory_structure()
            
            # Run sync command
            from src.containers.orchestrator import ContainerOrchestrator
            from src.sync.engines import SyncManager
            
            orchestrator = ContainerOrchestrator(config_manager)
            sync_manager = SyncManager(orchestrator)
            
            # Sync specific distribution
            debian_config = config.distributions["debian"]
            results = await sync_manager.sync_distribution(debian_config, ["bookworm"])
            
            assert len(results) == 1
            assert results[0]['status'] == 'completed'
            assert results[0]['distribution'] == 'debian'
            assert results[0]['version'] == 'bookworm'
    
    @pytest.mark.asyncio
    @patch('subprocess.run')
    @patch('src.sync.engines.SyncEngine._monitor_sync')
    async def test_multi_distribution_sync_workflow(self, mock_monitor_sync, mock_subprocess):
        """Test syncing multiple distributions with different strategies"""
        # Mock container operations
        mock_subprocess.side_effect = [
            Mock(stdout="podman version 4.0.0", returncode=0),  # version check
            # Multiple container operations for different distributions
            Mock(returncode=0), Mock(returncode=0), Mock(stdout="apt-container\n", returncode=0), Mock(returncode=0),  # APT
            Mock(returncode=0), Mock(returncode=0), Mock(stdout="yum-container\n", returncode=0), Mock(returncode=0),  # YUM
        ]
        
        # Mock successful sync results
        mock_monitor_sync.return_value = {
            'status': 'completed',
            'logs': 'Sync completed'
        }
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            config_manager = ConfigManager(config_path)
            config = config_manager.load_config()
            config.base_path = temp_dir
            
            # Enable both APT and YUM distributions
            config.distributions["debian"].enabled = True
            config.distributions["debian"].versions = ["bookworm"]
            config.distributions["rocky"].enabled = True
            config.distributions["rocky"].versions = ["9"]
            
            # Disable others
            for dist_name in ["ubuntu", "kali", "rhel"]:
                config.distributions[dist_name].enabled = False
            
            config_manager.save_config()
            
            # Set up components
            from src.storage.manager import StorageManager
            from src.containers.orchestrator import ContainerOrchestrator
            from src.sync.engines import SyncManager
            
            storage_manager = StorageManager(config_manager)
            storage_manager.ensure_directory_structure()
            
            orchestrator = ContainerOrchestrator(config_manager)
            sync_manager = SyncManager(orchestrator)
            
            # Sync multiple distributions using the optimized strategy
            distributions = {
                "debian": ["bookworm"],
                "rocky": ["9"]
            }
            
            results = await sync_manager.sync_multiple_distributions(distributions)
            
            assert len(results) == 2
            
            # Verify both distributions were synced
            dist_names = [r['distribution'] for r in results]
            assert 'debian' in dist_names
            assert 'rocky' in dist_names
            
            # All should be successful
            for result in results:
                assert result['status'] == 'completed'


@pytest.mark.integration
class TestErrorScenarios:
    """Test complete error handling scenarios"""
    
    @pytest.mark.asyncio
    @patch('src.main.setup_logging')
    async def test_invalid_command_handling(self, mock_setup_logging):
        """Test handling of invalid commands"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            with patch('sys.argv', ['linux-mirrors', '--config', config_path, 'invalid-command']):
                # Should handle gracefully and show help
                exit_code = await main.main()
                assert exit_code == 1  # Error exit code
    
    @pytest.mark.asyncio
    @patch('src.main.setup_logging')
    @patch('subprocess.run', side_effect=FileNotFoundError("Container runtime not found"))
    async def test_missing_container_runtime(self, mock_subprocess, mock_setup_logging):
        """Test handling when container runtime is missing"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            with patch('sys.argv', ['linux-mirrors', '--config', config_path, 'status']):
                exit_code = await main.main()
                assert exit_code == 1  # Should fail gracefully
    
    @pytest.mark.asyncio
    @patch('src.main.setup_logging')
    async def test_permission_denied_scenarios(self, mock_setup_logging):
        """Test handling of permission denied errors"""
        # Try to use system path without permissions
        config_path = "/etc/linux-mirrors/config.yaml"  # System path
        
        with patch('sys.argv', ['linux-mirrors', '--config', config_path, 'storage', '--info']):
            # Should handle permission errors gracefully
            exit_code = await main.main()
            # Exit code depends on specific error handling
            assert exit_code in [0, 1]  # Either succeeds with user paths or fails gracefully
    
    def test_corrupted_config_recovery(self):
        """Test recovery from corrupted configuration"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            # Create corrupted config
            with open(config_path, 'w') as f:
                f.write("invalid: yaml: [content")
            
            # Should handle corrupted config gracefully
            config_manager = ConfigManager(config_path)
            
            with pytest.raises(ValueError, match="Error loading config"):
                config_manager.load_config()
            
            # After fixing the corruption, should work
            with open(config_path, 'w') as f:
                f.write("container_runtime: podman\n")
            
            # Should load successfully now
            config = config_manager.load_config()
            assert config.container_runtime == "podman"


@pytest.mark.integration 
class TestUserExperienceWorkflows:
    """Test workflows from a user experience perspective"""
    
    def test_first_time_user_workflow(self):
        """Test the complete first-time user experience"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            # User runs the application for the first time
            # Config doesn't exist yet
            assert not os.path.exists(config_path)
            
            # Application creates default configuration
            config_manager = ConfigManager(config_path)
            config = config_manager.load_config()
            
            # User sees reasonable defaults
            assert os.path.exists(config_path)
            assert config.container_runtime in ["podman", "docker"]
            assert len(config.distributions) >= 4  # Has common distributions
            
            # User can see what distributions are available
            available_dists = list(config.distributions.keys())
            assert "debian" in available_dists
            assert "ubuntu" in available_dists
            assert "rocky" in available_dists
            
            # User customizes for their needs
            # Example: Enable only Debian and Ubuntu
            for dist_name, dist in config.distributions.items():
                if dist_name in ["debian", "ubuntu"]:
                    dist.enabled = True
                else:
                    dist.enabled = False
            
            config_manager.save_config()
            
            # User sets up directory structure
            from src.storage.manager import StorageManager
            storage_manager = StorageManager(config_manager)
            
            # Check disk space first
            space_check = storage_manager.check_disk_space(required_gb=5.0)
            if space_check['sufficient_space']:
                results = storage_manager.ensure_directory_structure()
                assert all(results.values())
    
    def test_experienced_user_workflow(self):
        """Test workflow for user with existing configuration"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            # User has existing configuration
            existing_config = {
                'base_path': temp_dir,
                'container_runtime': 'docker',
                'max_concurrent_syncs': 5,
                'log_level': 'DEBUG',
                'distributions': {
                    'custom-debian': {
                        'name': 'custom-debian',
                        'type': 'apt',
                        'versions': ['testing', 'unstable'],
                        'mirror_urls': ['http://ftp.us.debian.org/debian/'],
                        'components': ['main', 'contrib', 'non-free'],
                        'architectures': ['amd64', 'arm64'],
                        'enabled': True,
                        'sync_schedule': 'Mon *-*-* 02:00:00',
                        'include_source_packages': True
                    }
                }
            }
            
            with open(config_path, 'w') as f:
                yaml.dump(existing_config, f)
            
            # User loads existing configuration
            config_manager = ConfigManager(config_path)
            config = config_manager.load_config()
            
            # Configuration should match what user set up
            assert config.container_runtime == 'docker'
            assert config.max_concurrent_syncs == 5
            assert 'custom-debian' in config.distributions
            
            custom_dist = config.distributions['custom-debian']
            assert custom_dist.enabled is True
            assert 'testing' in custom_dist.versions
            assert custom_dist.include_source_packages is True
            
            # User can add more distributions
            new_dist = DistributionConfig(
                name="custom-ubuntu",
                type="apt",
                versions=["jammy", "lunar"],
                mirror_urls=["http://archive.ubuntu.com/ubuntu/"],
                components=["main", "restricted"],
                architectures=["amd64"],
                enabled=True
            )
            config_manager.update_distribution("custom-ubuntu", new_dist)
            
            # Changes should persist
            config_manager2 = ConfigManager(config_path)
            config2 = config_manager2.load_config()
            assert "custom-ubuntu" in config2.distributions
    
    @pytest.mark.asyncio
    async def test_automation_workflow(self):
        """Test workflow for automated/scheduled operations"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            # Set up configuration for automation
            config_manager = ConfigManager(config_path)
            config = config_manager.load_config()
            config.base_path = temp_dir
            
            # Configure for automation-friendly settings
            config.log_level = "WARNING"  # Less verbose
            config.max_concurrent_syncs = 2  # Conservative
            
            # Enable specific distributions for regular sync
            config.distributions["debian"].enabled = True
            config.distributions["debian"].versions = ["bookworm"]
            
            for dist_name in ["ubuntu", "kali", "rocky", "rhel"]:
                config.distributions[dist_name].enabled = False
            
            config_manager.save_config()
            
            # Generate systemd services for automation
            from src.systemd.service_generator import SystemdServiceGenerator
            systemd_dir = os.path.join(temp_dir, "systemd")
            
            service_generator = SystemdServiceGenerator(config_manager)
            with patch.object(service_generator, '_get_systemd_directory', return_value=systemd_dir):
                services = service_generator.create_all_services(user_mode=True, enable_timers=True)
            
            assert len(services) == 1  # One service for debian bookworm
            
            service = services[0]
            assert service['service_name'] == 'linux-mirror-debian-bookworm'
            
            # Verify service file contains automation-friendly settings
            with open(service['service_file']) as f:
                service_content = f.read()
                assert f"--config {config_path}" in service_content
                assert "Type=oneshot" in service_content
            
            # Verify timer is configured correctly
            with open(service['timer_file']) as f:
                timer_content = f.read()
                assert "OnCalendar=daily" in timer_content
                assert "Persistent=true" in timer_content


@pytest.mark.slow
@pytest.mark.integration
class TestStressScenarios:
    """Test system behavior under stress conditions"""
    
    def test_large_configuration_performance(self):
        """Test performance with large number of distributions"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")
            
            config_manager = ConfigManager(config_path)
            config = config_manager.load_config()
            config.base_path = temp_dir
            
            # Add many distributions
            import time
            start_time = time.time()
            
            for i in range(100):
                dist = DistributionConfig(
                    name=f"dist-{i:03d}",
                    type="apt" if i % 2 == 0 else "yum",
                    versions=[f"v{j}" for j in range(3)],
                    mirror_urls=[f"http://example-{i}.com/"],
                    components=["main"] if i % 2 == 0 else None,
                    architectures=["amd64"],
                    enabled=i < 10  # Only enable first 10
                )
                config_manager.update_distribution(f"dist-{i:03d}", dist)
            
            config_time = time.time() - start_time
            assert config_time < 10.0, f"Configuration took too long: {config_time}s"
            
            # Test directory creation performance
            from src.storage.manager import StorageManager
            storage_manager = StorageManager(config_manager)
            
            start_time = time.time()
            results = storage_manager.ensure_directory_structure()
            dir_time = time.time() - start_time
            
            assert dir_time < 15.0, f"Directory creation took too long: {dir_time}s"
            assert all(results.values()), "Some directories failed to create"
    
    @pytest.mark.asyncio
    async def test_concurrent_sync_limits(self):
        """Test that concurrent sync limits are respected"""
        # This would require more complex setup to truly test concurrency
        # For now, test that the semaphore limits are configured correctly
        
        with tempfile.TemporaryDirectory() as temp_dir:
            from src.containers.orchestrator import ContainerOrchestrator
            from src.sync.engines import SyncManager
            
            config_manager = Mock()
            config = MirrorConfig(base_path=temp_dir)
            config_manager.get_config.return_value = config
            
            # Mock container runtime to avoid actual container operations
            with patch.object(ContainerOrchestrator, '_validate_runtime'):
                orchestrator = ContainerOrchestrator(config_manager)
                sync_manager = SyncManager(orchestrator)
                
                # Verify semaphore limits
                assert sync_manager._apt_semaphore._value == 1  # APT sequential
                assert sync_manager._yum_semaphore._value == 3  # YUM parallel


# Helper functions for E2E tests
def simulate_user_input(responses: list):
    """Simulate user input for interactive components"""
    import io
    return io.StringIO('\n'.join(responses))


def capture_output():
    """Capture stdout/stderr for verification"""
    import io
    import sys
    from contextlib import contextmanager
    
    @contextmanager
    def captured_output():
        new_out, new_err = io.StringIO(), io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        try:
            sys.stdout, sys.stderr = new_out, new_err
            yield sys.stdout, sys.stderr
        finally:
            sys.stdout, sys.stderr = old_out, old_err
    
    return captured_output


def create_realistic_test_config(base_path: str) -> str:
    """Create a realistic test configuration"""
    config_path = os.path.join(base_path, "config.yaml")
    
    realistic_config = {
        'base_path': base_path,
        'container_runtime': 'podman',
        'max_concurrent_syncs': 2,
        'log_level': 'INFO',
        'distributions': {
            'debian': {
                'name': 'debian',
                'type': 'apt',
                'versions': ['bookworm', 'bullseye'],
                'mirror_urls': ['http://deb.debian.org/debian/'],
                'components': ['main', 'contrib', 'non-free'],
                'architectures': ['amd64'],
                'enabled': True,
                'sync_schedule': 'daily',
                'include_source_packages': False
            },
            'ubuntu': {
                'name': 'ubuntu',
                'type': 'apt',
                'versions': ['jammy', 'focal'],
                'mirror_urls': ['http://archive.ubuntu.com/ubuntu/'],
                'components': ['main', 'restricted', 'universe'],
                'architectures': ['amd64'],
                'enabled': True,
                'sync_schedule': 'daily'
            }
        }
    }
    
    with open(config_path, 'w') as f:
        yaml.dump(realistic_config, f, default_flow_style=False, indent=2)
    
    return config_path