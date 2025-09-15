#!/usr/bin/env python3

import os
import tempfile
import pytest
import yaml
from unittest.mock import patch, mock_open
from pathlib import Path

from src.config.manager import ConfigManager, DistributionConfig, MirrorConfig


class TestDistributionConfig:
    """Test DistributionConfig dataclass functionality"""
    
    def test_distribution_config_creation(self):
        """Test creating a distribution config with all fields"""
        config = DistributionConfig(
            name="debian",
            type="apt",
            versions=["bookworm", "bullseye"],
            mirror_urls=["http://deb.debian.org/debian/"],
            components=["main", "contrib", "non-free"],
            architectures=["amd64", "arm64"],
            enabled=True,
            sync_schedule="daily"
        )
        
        assert config.name == "debian"
        assert config.type == "apt"
        assert config.versions == ["bookworm", "bullseye"]
        assert config.enabled is True
        assert config.include_gpg_keys is True  # Default value
    
    def test_distribution_config_defaults(self):
        """Test distribution config with minimal required fields"""
        config = DistributionConfig(
            name="ubuntu",
            type="apt",
            versions=["focal"],
            mirror_urls=["http://archive.ubuntu.com/ubuntu/"]
        )
        
        assert config.components is None  # Default
        assert config.enabled is True  # Default
        assert config.sync_schedule == "daily"  # Default


class TestMirrorConfig:
    """Test MirrorConfig dataclass functionality"""
    
    def test_mirror_config_defaults_as_root(self):
        """Test default paths when running as root"""
        with patch('os.geteuid', return_value=0):
            config = MirrorConfig()
            
            assert config.base_path == "/srv/mirror"
            assert config.apt_path == "/srv/mirror/apt"
            assert config.yum_path == "/srv/mirror/yum"
    
    def test_mirror_config_defaults_as_user(self):
        """Test default paths when running as regular user"""
        with patch('os.geteuid', return_value=1000), \
             patch('os.path.expanduser', return_value="/home/user/mirrors"):
            config = MirrorConfig()
            
            assert config.base_path == "/home/user/mirrors"
            assert config.apt_path == "/home/user/mirrors/apt"
            assert config.yum_path == "/home/user/mirrors/yum"
    
    def test_mirror_config_custom_paths(self):
        """Test custom paths override defaults"""
        config = MirrorConfig(
            base_path="/custom/mirror",
            apt_path="/custom/apt",
            yum_path="/custom/yum"
        )
        
        assert config.base_path == "/custom/mirror"
        assert config.apt_path == "/custom/apt"
        assert config.yum_path == "/custom/yum"
    
    def test_default_distributions_included(self):
        """Test that default distributions are created"""
        config = MirrorConfig()
        
        expected_distributions = ["debian", "ubuntu", "kali", "rocky", "rhel"]
        assert set(config.distributions.keys()) == set(expected_distributions)
        
        # Test specific distribution properties
        debian = config.distributions["debian"]
        assert debian.type == "apt"
        assert "bookworm" in debian.versions
        assert "main" in debian.components
        
        rocky = config.distributions["rocky"]
        assert rocky.type == "yum"
        assert "8" in rocky.versions
        assert rocky.enabled is True
        
        rhel = config.distributions["rhel"]
        assert rhel.enabled is False  # Requires subscription


class TestConfigManager:
    """Test ConfigManager functionality"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.yaml")
    
    def teardown_method(self):
        """Clean up test fixtures"""
        if os.path.exists(self.temp_dir):
            import shutil
            shutil.rmtree(self.temp_dir)
    
    def test_config_manager_init_default_path(self):
        """Test config manager initialization with default path"""
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': '/tmp/.config'}):
            manager = ConfigManager()
            expected_path = "/tmp/.config/linux-mirrors/config.yaml"
            assert manager.config_path == expected_path
    
    def test_config_manager_init_custom_path(self):
        """Test config manager initialization with custom path"""
        manager = ConfigManager(self.config_path)
        assert manager.config_path == self.config_path
    
    def test_load_config_file_not_exists(self):
        """Test loading config when file doesn't exist creates default"""
        manager = ConfigManager(self.config_path)
        config = manager.load_config()
        
        assert isinstance(config, MirrorConfig)
        assert os.path.exists(self.config_path)  # Should be created
        
        # Verify default values
        assert len(config.distributions) == 5
        assert config.container_runtime == "podman"
    
    def test_load_config_valid_file(self):
        """Test loading valid config file"""
        config_data = {
            'base_path': '/custom/mirror',
            'container_runtime': 'docker',
            'max_concurrent_syncs': 5,
            'distributions': {
                'test-dist': {
                    'name': 'test-dist',
                    'type': 'apt',
                    'versions': ['test-version'],
                    'mirror_urls': ['http://example.com'],
                    'enabled': True
                }
            }
        }
        
        with open(self.config_path, 'w') as f:
            yaml.dump(config_data, f)
        
        manager = ConfigManager(self.config_path)
        config = manager.load_config()
        
        assert config.base_path == '/custom/mirror'
        assert config.container_runtime == 'docker'
        assert config.max_concurrent_syncs == 5
        assert 'test-dist' in config.distributions
        
        test_dist = config.distributions['test-dist']
        assert isinstance(test_dist, DistributionConfig)
        assert test_dist.name == 'test-dist'
        assert test_dist.versions == ['test-version']
    
    def test_load_config_invalid_yaml(self):
        """Test loading invalid YAML file raises error"""
        with open(self.config_path, 'w') as f:
            f.write("invalid: yaml: content: [")
        
        manager = ConfigManager(self.config_path)
        
        with pytest.raises(ValueError, match="Error loading config"):
            manager.load_config()
    
    def test_save_config(self):
        """Test saving configuration to file"""
        manager = ConfigManager(self.config_path)
        config = manager.load_config()
        
        # Modify config
        config.container_runtime = "docker"
        config.max_concurrent_syncs = 8
        
        manager.save_config()
        
        # Load from file and verify changes
        with open(self.config_path, 'r') as f:
            saved_data = yaml.safe_load(f)
        
        assert saved_data['container_runtime'] == "docker"
        assert saved_data['max_concurrent_syncs'] == 8
    
    def test_get_config_caching(self):
        """Test that get_config caches the loaded config"""
        manager = ConfigManager(self.config_path)
        
        config1 = manager.get_config()
        config2 = manager.get_config()
        
        # Should be the same object (cached)
        assert config1 is config2
    
    def test_update_distribution(self):
        """Test updating a distribution configuration"""
        manager = ConfigManager(self.config_path)
        manager.load_config()
        
        new_dist = DistributionConfig(
            name="test-dist",
            type="yum",
            versions=["9"],
            mirror_urls=["http://example.com"]
        )
        
        manager.update_distribution("test-dist", new_dist)
        
        # Verify it's saved
        assert os.path.exists(self.config_path)
        
        # Verify it can be loaded back
        manager._config = None  # Clear cache
        config = manager.load_config()
        assert "test-dist" in config.distributions
        assert config.distributions["test-dist"].type == "yum"
    
    def test_get_enabled_distributions(self):
        """Test filtering enabled distributions"""
        manager = ConfigManager(self.config_path)
        config = manager.load_config()
        
        # Enable some, disable others
        config.distributions["debian"].enabled = True
        config.distributions["ubuntu"].enabled = False
        config.distributions["rocky"].enabled = True
        
        enabled = manager.get_enabled_distributions()
        
        assert "debian" in enabled
        assert "ubuntu" not in enabled
        assert "rocky" in enabled
    
    def test_get_distribution_path_apt(self):
        """Test getting path for APT distribution"""
        manager = ConfigManager(self.config_path)
        config = manager.load_config()
        
        path = manager.get_distribution_path("debian")
        expected_path = os.path.join(config.apt_path, "debian")
        assert path == expected_path
    
    def test_get_distribution_path_yum(self):
        """Test getting path for YUM distribution"""
        manager = ConfigManager(self.config_path)
        config = manager.load_config()
        
        path = manager.get_distribution_path("rocky")
        expected_path = os.path.join(config.yum_path, "rocky")
        assert path == expected_path
    
    def test_get_distribution_path_unknown(self):
        """Test getting path for unknown distribution raises error"""
        manager = ConfigManager(self.config_path)
        manager.load_config()
        
        with pytest.raises(ValueError, match="Unknown distribution"):
            manager.get_distribution_path("nonexistent")
    
    def test_path_recalculation_on_permission_change(self):
        """Test that paths are recalculated when permissions change"""
        config_data = {
            'base_path': '/srv/mirror',
            'apt_path': '/srv/mirror/apt',
            'yum_path': '/srv/mirror/yum'
        }
        
        with open(self.config_path, 'w') as f:
            yaml.dump(config_data, f)
        
        # Load as non-root user
        with patch('os.geteuid', return_value=1000), \
             patch('os.path.expanduser', return_value="/home/user/mirrors"):
            manager = ConfigManager(self.config_path)
            config = manager.load_config()
            
            # Paths should be recalculated for user
            assert config.base_path == "/home/user/mirrors"
            assert config.apt_path == "/home/user/mirrors/apt"
            assert config.yum_path == "/home/user/mirrors/yum"


@pytest.mark.integration
class TestConfigManagerIntegration:
    """Integration tests for ConfigManager with real file system"""
    
    def test_full_config_lifecycle(self):
        """Test complete config lifecycle: create, modify, save, reload"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "test_config.yaml")
            
            # Create manager and load default config
            manager = ConfigManager(config_path)
            config = manager.load_config()
            
            # Modify configuration
            config.container_runtime = "docker"
            config.max_concurrent_syncs = 2
            
            # Add new distribution
            new_dist = DistributionConfig(
                name="custom",
                type="apt",
                versions=["stable"],
                mirror_urls=["http://custom.example.com/"],
                enabled=False
            )
            manager.update_distribution("custom", new_dist)
            
            # Create new manager instance to test persistence
            manager2 = ConfigManager(config_path)
            config2 = manager2.load_config()
            
            # Verify changes persisted
            assert config2.container_runtime == "docker"
            assert config2.max_concurrent_syncs == 2
            assert "custom" in config2.distributions
            assert config2.distributions["custom"].enabled is False
    
    def test_config_directory_creation(self):
        """Test that config directory is created if it doesn't exist"""
        with tempfile.TemporaryDirectory() as temp_dir:
            nested_path = os.path.join(temp_dir, "nested", "dir", "config.yaml")

            manager = ConfigManager(nested_path)
            manager.load_config()
            manager.save_config()

            assert os.path.exists(nested_path)
            assert os.path.exists(os.path.dirname(nested_path))

    def test_epel_default_configuration(self):
        """Test that EPEL is included in default configuration"""
        # Test the default configuration directly instead of loaded config
        from src.config.manager import MirrorConfig
        config = MirrorConfig()

        # Verify EPEL is present in default distributions
        assert "epel" in config.distributions
        epel_config = config.distributions["epel"]

        # Verify EPEL configuration properties
        assert epel_config.name == "epel"
        assert epel_config.type == "yum"
        assert epel_config.enabled is True
        assert "8" in epel_config.versions
        assert "9" in epel_config.versions
        assert "10" in epel_config.versions
        assert "x86_64" in epel_config.architectures
        assert "aarch64" in epel_config.architectures
        assert epel_config.components == ["Everything"]
        assert epel_config.include_gpg_keys is True
        assert len(epel_config.gpg_key_urls) == 3  # Keys for versions 8, 9, 10
        assert "https://dl.fedoraproject.org/pub/epel" in epel_config.mirror_urls[0]

    def test_epel_custom_configuration(self):
        """Test creating custom EPEL configuration"""
        epel_config = DistributionConfig(
            name="epel",
            type="yum",
            versions=["9"],  # Only EPEL 9
            mirror_urls=["https://custom-mirror.example.com/epel"],
            components=["Everything"],
            architectures=["x86_64"],
            enabled=True,
            include_gpg_keys=True,
            gpg_key_urls=["https://custom-mirror.example.com/RPM-GPG-KEY-EPEL-9"]
        )

        assert epel_config.name == "epel"
        assert epel_config.type == "yum"
        assert epel_config.versions == ["9"]
        assert epel_config.architectures == ["x86_64"]
        assert epel_config.include_gpg_keys is True