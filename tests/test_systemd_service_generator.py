#!/usr/bin/env python3

import os
import tempfile
import pytest
from unittest.mock import Mock, patch, mock_open
from pathlib import Path

from src.config.manager import ConfigManager, DistributionConfig, MirrorConfig
from src.systemd.service_generator import SystemdServiceGenerator


class TestSystemdServiceGenerator:
    """Test SystemdServiceGenerator functionality"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        
        # Create mock config manager
        self.mock_config_manager = Mock(spec=ConfigManager)
        self.mock_config = MirrorConfig(base_path=self.temp_dir)
        
        # Add test distributions
        self.mock_config.distributions = {
            "debian": DistributionConfig(
                name="debian",
                type="apt",
                versions=["bookworm", "bullseye"],
                mirror_urls=["http://deb.debian.org/debian/"],
                components=["main"],
                architectures=["amd64"],
                enabled=True,
                sync_schedule="daily"
            ),
            "rocky": DistributionConfig(
                name="rocky",
                type="yum", 
                versions=["9"],
                mirror_urls=["https://download.rockylinux.org/pub/rocky/"],
                architectures=["x86_64"],
                enabled=True,
                sync_schedule="weekly"
            ),
            "disabled": DistributionConfig(
                name="disabled",
                type="apt",
                versions=["stable"],
                mirror_urls=["http://example.com/"],
                components=["main"],
                architectures=["amd64"],
                enabled=False
            )
        }
        
        self.mock_config_manager.get_config.return_value = self.mock_config
    
    def teardown_method(self):
        """Clean up test fixtures"""
        if os.path.exists(self.temp_dir):
            import shutil
            shutil.rmtree(self.temp_dir)
    
    def test_systemd_service_generator_init(self):
        """Test SystemdServiceGenerator initialization"""
        generator = SystemdServiceGenerator(self.mock_config_manager)
        
        assert generator.config_manager == self.mock_config_manager
        assert generator.config == self.mock_config
    
    @patch('os.geteuid', return_value=0)
    def test_get_systemd_directory_system(self, mock_geteuid):
        """Test systemd directory for system services"""
        generator = SystemdServiceGenerator(self.mock_config_manager)
        
        directory = generator._get_systemd_directory(user_mode=False)
        assert directory == "/etc/systemd/system"
    
    @patch('os.geteuid', return_value=1000)
    @patch('os.path.expanduser', return_value="/home/user")
    def test_get_systemd_directory_user(self, mock_expanduser, mock_geteuid):
        """Test systemd directory for user services"""
        generator = SystemdServiceGenerator(self.mock_config_manager)
        
        directory = generator._get_systemd_directory(user_mode=True)
        assert directory == "/home/user/.config/systemd/user"
    
    def test_generate_service_name(self):
        """Test service name generation"""
        generator = SystemdServiceGenerator(self.mock_config_manager)
        
        name = generator._generate_service_name("debian", "bookworm")
        assert name == "linux-mirror-debian-bookworm"
    
    def test_generate_service_content(self):
        """Test service unit content generation"""
        generator = SystemdServiceGenerator(self.mock_config_manager)
        
        content = generator._generate_service_content("debian", "bookworm")
        
        # Check required sections
        assert "[Unit]" in content
        assert "[Service]" in content
        assert "[Install]" in content
        
        # Check service description
        assert "Linux Mirror Sync - debian bookworm" in content
        
        # Check service type and execution
        assert "Type=oneshot" in content
        assert "linux-mirrors sync --distribution debian --version bookworm" in content
        
        # Check dependencies
        assert "After=network-online.target" in content
        assert "Wants=network-online.target" in content
    
    def test_generate_service_content_with_config(self):
        """Test service content with custom config path"""
        custom_config = "/custom/config.yaml"
        self.mock_config_manager.config_path = custom_config
        
        generator = SystemdServiceGenerator(self.mock_config_manager)
        content = generator._generate_service_content("debian", "bookworm")
        
        assert f"--config {custom_config}" in content
    
    def test_generate_timer_content_daily(self):
        """Test timer unit content for daily schedule"""
        generator = SystemdServiceGenerator(self.mock_config_manager)
        
        content = generator._generate_timer_content("linux-mirror-debian-bookworm", "daily")
        
        assert "[Unit]" in content
        assert "[Timer]" in content
        assert "[Install]" in content
        
        # Check timer configuration
        assert "OnCalendar=daily" in content
        assert "Persistent=true" in content
        assert "WantedBy=timers.target" in content
        assert "linux-mirror-debian-bookworm.service" in content
    
    def test_generate_timer_content_weekly(self):
        """Test timer unit content for weekly schedule"""
        generator = SystemdServiceGenerator(self.mock_config_manager)
        
        content = generator._generate_timer_content("linux-mirror-rocky-9", "weekly")
        
        assert "OnCalendar=weekly" in content
    
    def test_generate_timer_content_custom_schedule(self):
        """Test timer unit content with custom schedule"""
        generator = SystemdServiceGenerator(self.mock_config_manager)
        
        # Test custom OnCalendar format
        content = generator._generate_timer_content("test-service", "Mon *-*-* 02:00:00")
        
        assert "OnCalendar=Mon *-*-* 02:00:00" in content
    
    @patch('os.makedirs')
    @patch('builtins.open', new_callable=mock_open)
    def test_create_service_files_success(self, mock_file, mock_makedirs):
        """Test successful service file creation"""
        generator = SystemdServiceGenerator(self.mock_config_manager)
        
        with patch.object(generator, '_get_systemd_directory', return_value="/etc/systemd/system"):
            result = generator._create_service_files("debian", "bookworm", user_mode=False, enable_timers=True)
        
        assert result is not None
        assert result['service_name'] == 'linux-mirror-debian-bookworm'
        assert result['distribution'] == 'debian'
        assert result['version'] == 'bookworm'
        assert 'service_file' in result
        assert 'timer_file' in result
        
        # Check that files were written
        assert mock_file.call_count == 2  # service + timer files
        mock_makedirs.assert_called_once()
    
    @patch('os.makedirs')
    @patch('builtins.open', new_callable=mock_open)
    def test_create_service_files_no_timer(self, mock_file, mock_makedirs):
        """Test service file creation without timer"""
        generator = SystemdServiceGenerator(self.mock_config_manager)
        
        with patch.object(generator, '_get_systemd_directory', return_value="/etc/systemd/system"):
            result = generator._create_service_files("debian", "bookworm", user_mode=False, enable_timers=False)
        
        assert result is not None
        assert 'timer_file' not in result
        
        # Only service file should be written
        assert mock_file.call_count == 1
    
    @patch('os.makedirs', side_effect=PermissionError("Permission denied"))
    def test_create_service_files_permission_error(self, mock_makedirs):
        """Test service file creation with permission error"""
        generator = SystemdServiceGenerator(self.mock_config_manager)
        
        with patch.object(generator, '_get_systemd_directory', return_value="/etc/systemd/system"):
            result = generator._create_service_files("debian", "bookworm", user_mode=False, enable_timers=True)
        
        assert result is None
    
    @patch('os.makedirs')
    @patch('builtins.open', side_effect=IOError("Write failed"))
    def test_create_service_files_write_error(self, mock_file, mock_makedirs):
        """Test service file creation with write error"""
        generator = SystemdServiceGenerator(self.mock_config_manager)
        
        with patch.object(generator, '_get_systemd_directory', return_value="/etc/systemd/system"):
            result = generator._create_service_files("debian", "bookworm", user_mode=False, enable_timers=True)
        
        assert result is None
    
    @patch.object(SystemdServiceGenerator, '_create_service_files')
    def test_create_all_services_enabled_only(self, mock_create_service_files):
        """Test creating services only for enabled distributions"""
        # Mock successful service creation
        mock_create_service_files.side_effect = [
            {'service_name': 'linux-mirror-debian-bookworm', 'distribution': 'debian', 'version': 'bookworm'},
            {'service_name': 'linux-mirror-debian-bullseye', 'distribution': 'debian', 'version': 'bullseye'},
            {'service_name': 'linux-mirror-rocky-9', 'distribution': 'rocky', 'version': '9'}
        ]
        
        generator = SystemdServiceGenerator(self.mock_config_manager)
        created_services = generator.create_all_services(user_mode=False, enable_timers=True)
        
        # Should create services for enabled distributions only
        # debian (2 versions) + rocky (1 version) = 3 services
        assert len(created_services) == 3
        
        # Verify calls were made for enabled distributions only
        assert mock_create_service_files.call_count == 3
        
        # Check that disabled distribution was not processed
        calls = mock_create_service_files.call_args_list
        call_dists = [call[0][0] for call in calls]  # First argument (distribution name)
        assert "disabled" not in call_dists
        assert "debian" in call_dists
        assert "rocky" in call_dists
    
    @patch.object(SystemdServiceGenerator, '_create_service_files')
    def test_create_all_services_with_failures(self, mock_create_service_files):
        """Test creating services with some failures"""
        # Mock partial failures
        mock_create_service_files.side_effect = [
            {'service_name': 'linux-mirror-debian-bookworm', 'distribution': 'debian', 'version': 'bookworm'},
            None,  # Failure
            {'service_name': 'linux-mirror-rocky-9', 'distribution': 'rocky', 'version': '9'}
        ]
        
        generator = SystemdServiceGenerator(self.mock_config_manager)
        created_services = generator.create_all_services(user_mode=False, enable_timers=True)
        
        # Should return only successful services
        assert len(created_services) == 2
        
        service_names = [s['service_name'] for s in created_services]
        assert 'linux-mirror-debian-bookworm' in service_names
        assert 'linux-mirror-rocky-9' in service_names
    
    @patch.object(SystemdServiceGenerator, '_create_service_files')
    def test_create_all_services_user_mode(self, mock_create_service_files):
        """Test creating services in user mode"""
        mock_create_service_files.return_value = {
            'service_name': 'linux-mirror-debian-bookworm',
            'distribution': 'debian',
            'version': 'bookworm'
        }
        
        generator = SystemdServiceGenerator(self.mock_config_manager)
        created_services = generator.create_all_services(user_mode=True, enable_timers=False)
        
        # Verify user_mode was passed to _create_service_files
        for call in mock_create_service_files.call_args_list:
            args, kwargs = call
            assert kwargs.get('user_mode') is True
            assert kwargs.get('enable_timers') is False
    
    def test_create_all_services_empty_config(self):
        """Test creating services with no enabled distributions"""
        # Disable all distributions
        for dist_name in self.mock_config.distributions:
            self.mock_config.distributions[dist_name].enabled = False
        
        generator = SystemdServiceGenerator(self.mock_config_manager)
        created_services = generator.create_all_services(user_mode=False, enable_timers=True)
        
        assert created_services == []


@pytest.mark.integration
class TestSystemdServiceGeneratorIntegration:
    """Integration tests for SystemdServiceGenerator with real filesystem"""
    
    def test_full_service_creation_lifecycle(self):
        """Test complete service creation with real filesystem"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create mock config
            config = MirrorConfig(base_path=temp_dir)
            config.distributions = {
                "test-dist": DistributionConfig(
                    name="test-dist",
                    type="apt",
                    versions=["stable"],
                    mirror_urls=["http://example.com/"],
                    components=["main"],
                    architectures=["amd64"],
                    enabled=True,
                    sync_schedule="daily"
                )
            }
            
            config_manager = Mock()
            config_manager.get_config.return_value = config
            config_manager.config_path = None
            
            # Use temp directory as systemd directory
            systemd_dir = os.path.join(temp_dir, "systemd")
            
            generator = SystemdServiceGenerator(config_manager)
            
            with patch.object(generator, '_get_systemd_directory', return_value=systemd_dir):
                created_services = generator.create_all_services(user_mode=False, enable_timers=True)
            
            assert len(created_services) == 1
            
            service = created_services[0]
            assert service['service_name'] == 'linux-mirror-test-dist-stable'
            
            # Check that files were actually created
            service_file = service['service_file']
            timer_file = service['timer_file']
            
            assert os.path.exists(service_file)
            assert os.path.exists(timer_file)
            
            # Verify file contents
            with open(service_file) as f:
                service_content = f.read()
                assert "linux-mirrors sync --distribution test-dist --version stable" in service_content
            
            with open(timer_file) as f:
                timer_content = f.read()
                assert "OnCalendar=daily" in timer_content
    
    def test_service_file_permissions(self):
        """Test that service files are created with correct permissions"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = MirrorConfig(base_path=temp_dir)
            config.distributions = {
                "test": DistributionConfig(
                    name="test",
                    type="apt",
                    versions=["stable"],
                    mirror_urls=["http://example.com/"],
                    components=["main"],
                    architectures=["amd64"],
                    enabled=True
                )
            }
            
            config_manager = Mock()
            config_manager.get_config.return_value = config
            config_manager.config_path = None
            
            systemd_dir = os.path.join(temp_dir, "systemd")
            
            generator = SystemdServiceGenerator(config_manager)
            
            with patch.object(generator, '_get_systemd_directory', return_value=systemd_dir):
                created_services = generator.create_all_services(user_mode=False, enable_timers=True)
            
            service_file = created_services[0]['service_file']
            
            # Check file permissions (should be readable by all)
            stat = os.stat(service_file)
            assert stat.st_mode & 0o644  # At least readable by owner and group


class TestSystemdServiceGeneratorEdgeCases:
    """Test edge cases and error conditions"""
    
    def test_invalid_schedule_format(self):
        """Test handling of invalid schedule formats"""
        config_manager = Mock()
        config = MirrorConfig()
        config_manager.get_config.return_value = config
        
        generator = SystemdServiceGenerator(config_manager)
        
        # Should handle invalid schedule gracefully
        content = generator._generate_timer_content("test-service", "invalid-schedule")
        
        # Should default to using the schedule as-is (systemd will validate)
        assert "OnCalendar=invalid-schedule" in content
    
    def test_long_distribution_names(self):
        """Test handling of long distribution names"""
        config_manager = Mock()
        config = MirrorConfig()
        config_manager.get_config.return_value = config
        
        generator = SystemdServiceGenerator(config_manager)
        
        long_name = "very-long-distribution-name-that-might-cause-issues"
        service_name = generator._generate_service_name(long_name, "version")
        
        # Should still generate valid service name
        assert service_name.startswith("linux-mirror-")
        assert long_name in service_name
        
        # Should be valid systemd service name (no spaces, valid characters)
        import re
        assert re.match(r'^[a-zA-Z0-9_.-]+$', service_name)
    
    def test_special_characters_in_names(self):
        """Test handling of special characters in distribution/version names"""
        config_manager = Mock()
        config = MirrorConfig()
        config_manager.get_config.return_value = config
        
        generator = SystemdServiceGenerator(config_manager)
        
        # Test with version containing dots and special chars
        service_name = generator._generate_service_name("dist", "20.04.1-special")
        
        # Should sanitize or handle special characters appropriately
        assert "linux-mirror-dist-20.04.1-special" == service_name or \
               service_name.replace(".", "-").replace("_", "-") == service_name