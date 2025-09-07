#!/usr/bin/env python3

import os
import tempfile
import shutil
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
from pathlib import Path

from src.config.manager import ConfigManager, DistributionConfig, MirrorConfig
from src.storage.manager import StorageManager


class TestStorageManager:
    """Test StorageManager functionality"""
    
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
                enabled=True
            ),
            "rocky": DistributionConfig(
                name="rocky",
                type="yum",
                versions=["9"],
                mirror_urls=["https://download.rockylinux.org/pub/rocky/"],
                architectures=["x86_64"],
                enabled=True
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
        self.mock_config_manager.get_distribution_path.side_effect = self._get_distribution_path
    
    def teardown_method(self):
        """Clean up test fixtures"""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
    
    def _get_distribution_path(self, dist_name: str) -> str:
        """Mock distribution path getter"""
        dist_config = self.mock_config.distributions[dist_name]
        if dist_config.type == "apt":
            return os.path.join(self.mock_config.apt_path, dist_name)
        else:
            return os.path.join(self.mock_config.yum_path, dist_name)
    
    def test_storage_manager_init(self):
        """Test StorageManager initialization"""
        manager = StorageManager(self.mock_config_manager)
        
        assert manager.config_manager == self.mock_config_manager
        assert manager.config == self.mock_config
    
    def test_ensure_directory_structure_success(self):
        """Test successful directory structure creation"""
        manager = StorageManager(self.mock_config_manager)
        
        results = manager.ensure_directory_structure()
        
        # Check that base directories were created
        assert os.path.exists(self.mock_config.base_path)
        assert os.path.exists(self.mock_config.apt_path)
        assert os.path.exists(self.mock_config.yum_path)
        
        # Check that enabled distribution directories were created
        debian_path = os.path.join(self.mock_config.apt_path, "debian")
        assert os.path.exists(debian_path)
        assert os.path.exists(os.path.join(debian_path, "bookworm"))
        assert os.path.exists(os.path.join(debian_path, "bullseye"))
        
        rocky_path = os.path.join(self.mock_config.yum_path, "rocky")
        assert os.path.exists(rocky_path)
        assert os.path.exists(os.path.join(rocky_path, "9"))
        
        # Check that disabled distribution directories were not created
        disabled_path = os.path.join(self.mock_config.apt_path, "disabled")
        assert not os.path.exists(disabled_path)
        
        # All results should be successful
        assert all(results.values())
    
    @patch('os.makedirs')
    def test_ensure_directory_structure_partial_failure(self, mock_makedirs):
        """Test directory creation with some failures"""
        # Make some directory creations fail
        def makedirs_side_effect(path, mode=0o755, exist_ok=True):
            if "failure" in path:
                raise PermissionError("Permission denied")
            return None
        
        mock_makedirs.side_effect = makedirs_side_effect
        
        # Add a directory that will fail
        self.mock_config.distributions["failure"] = DistributionConfig(
            name="failure",
            type="apt",
            versions=["stable"],
            mirror_urls=["http://example.com/"],
            components=["main"],
            architectures=["amd64"],
            enabled=True
        )
        
        manager = StorageManager(self.mock_config_manager)
        results = manager.ensure_directory_structure()
        
        # Some should succeed, some should fail
        assert not all(results.values())
        assert any(results.values())
    
    def test_get_storage_info_success(self):
        """Test successful storage info retrieval"""
        # Create the expected directories first
        os.makedirs(self.mock_config.apt_path, exist_ok=True)
        os.makedirs(self.mock_config.yum_path, exist_ok=True)
        
        with patch('psutil.disk_usage') as mock_disk_usage, \
             patch('os.listdir', return_value=['subdir1', 'subdir2']), \
             patch('os.path.isdir', return_value=True), \
             patch.object(StorageManager, '_get_directory_size', return_value=100 * 1024**2):
            
            # Mock disk usage
            mock_disk_usage.return_value = Mock(
                total=1000 * 1024**3,  # 1000 GB
                used=400 * 1024**3,    # 400 GB  
                free=600 * 1024**3     # 600 GB
            )
            
            manager = StorageManager(self.mock_config_manager)
            storage_info = manager.get_storage_info()
        
        assert storage_info['base_path'] == self.temp_dir
        assert storage_info['total_repos'] >= 0
        assert len(storage_info['paths']) >= 3  # base, apt, yum paths
        
        # Check path info structure
        for path_info in storage_info['paths']:
            assert 'path' in path_info
            assert 'type' in path_info
            assert 'used_percent' in path_info
            assert path_info['used_percent'] == 40.0  # 400/1000 * 100
    
    @patch('os.path.exists', return_value=False)
    def test_get_storage_info_nonexistent_paths(self, mock_exists):
        """Test storage info with nonexistent paths"""
        manager = StorageManager(self.mock_config_manager)
        storage_info = manager.get_storage_info()
        
        assert storage_info['base_path'] == self.temp_dir
        assert len(storage_info['paths']) == 0  # No paths should be included
    
    def test_get_path_info_nonexistent(self):
        """Test path info for nonexistent path"""
        manager = StorageManager(self.mock_config_manager)
        
        path_info = manager._get_path_info("/nonexistent/path", "test")
        
        assert path_info is None
    
    @patch('psutil.disk_usage')
    @patch('os.listdir', return_value=['dir1', 'dir2'])
    @patch('os.path.isdir', return_value=True)
    def test_get_path_info_success(self, mock_isdir, mock_listdir, mock_disk_usage):
        """Test successful path info retrieval"""
        # Create a real directory for testing
        test_path = os.path.join(self.temp_dir, "test_path")
        os.makedirs(test_path, exist_ok=True)
        
        # Mock disk usage
        mock_disk_usage.return_value = Mock(
            total=1000 * 1024**3,
            used=400 * 1024**3,
            free=600 * 1024**3
        )
        
        with patch.object(StorageManager, '_get_directory_size', return_value=50 * 1024**2):
            manager = StorageManager(self.mock_config_manager)
            path_info = manager._get_path_info(test_path, "test")
        
        assert path_info['path'] == test_path
        assert path_info['type'] == "test"
        assert path_info['used_percent'] == 40.0
        assert path_info['repo_count'] == 2  # From mock_listdir
        assert path_info['directory_size'] == 50 * 1024**2
    
    def test_get_directory_size(self):
        """Test directory size calculation"""
        # Create test directory with files
        test_dir = os.path.join(self.temp_dir, "size_test")
        os.makedirs(test_dir, exist_ok=True)
        
        # Create test files with known sizes
        file1 = os.path.join(test_dir, "file1.txt")
        file2 = os.path.join(test_dir, "file2.txt")
        
        with open(file1, 'w') as f:
            f.write("a" * 100)  # 100 bytes
        
        with open(file2, 'w') as f:
            f.write("b" * 200)  # 200 bytes
        
        manager = StorageManager(self.mock_config_manager)
        size = manager._get_directory_size(test_dir)
        
        assert size == 300  # 100 + 200 bytes
    
    def test_get_directory_size_with_subdirectories(self):
        """Test directory size calculation with subdirectories"""
        # Create nested directory structure
        test_dir = os.path.join(self.temp_dir, "nested_test")
        sub_dir = os.path.join(test_dir, "subdir")
        os.makedirs(sub_dir, exist_ok=True)
        
        # Create files at different levels
        with open(os.path.join(test_dir, "root.txt"), 'w') as f:
            f.write("a" * 50)
        
        with open(os.path.join(sub_dir, "sub.txt"), 'w') as f:
            f.write("b" * 75)
        
        manager = StorageManager(self.mock_config_manager)
        size = manager._get_directory_size(test_dir)
        
        assert size == 125  # 50 + 75 bytes
    
    @patch('os.path.exists', return_value=True)
    @patch('os.path.isdir', return_value=True)
    def test_count_repositories(self, mock_isdir, mock_exists):
        """Test repository counting"""
        # Mock os.listdir to return appropriate directories for each distribution
        def mock_listdir_side_effect(path):
            if "debian" in path:
                return ['bookworm', 'bullseye']
            elif "rocky" in path:
                return ['9']
            elif "disabled" in path:
                return ['stable']
            else:
                return []
        
        with patch('os.listdir', side_effect=mock_listdir_side_effect):
            manager = StorageManager(self.mock_config_manager)
            count = manager._count_repositories()
        
        # Should count directories for enabled distributions only
        # debian (2 versions) + rocky (1 version) = 3 total
        expected_count = sum(len(d.versions) for d in self.mock_config.distributions.values() if d.enabled)
        assert count == expected_count
    
    def test_count_repositories_disabled_filtered(self):
        """Test that disabled distributions are not counted"""
        # Only enable one distribution
        self.mock_config.distributions["debian"].enabled = False
        self.mock_config.distributions["rocky"].enabled = True
        
        with patch('os.path.exists', return_value=True), \
             patch('os.listdir', return_value=['9']), \
             patch('os.path.isdir', return_value=True):
            
            manager = StorageManager(self.mock_config_manager)
            count = manager._count_repositories()
            
            assert count == 1  # Only rocky with 1 version
    
    def test_cleanup_old_syncs(self):
        """Test cleanup of old sync data"""
        # Create test directory with old files
        test_dist_dir = os.path.join(self.temp_dir, "apt", "debian")
        os.makedirs(test_dist_dir, exist_ok=True)
        
        # Create old files that should be cleaned up
        old_time = (datetime.now() - timedelta(days=60)).timestamp()
        
        old_file1 = os.path.join(test_dist_dir, "old.tmp")
        old_file2 = os.path.join(test_dist_dir, "cache.lock")
        new_file = os.path.join(test_dist_dir, "current.deb")
        
        # Create files
        with open(old_file1, 'w') as f:
            f.write("old temporary file")
        with open(old_file2, 'w') as f:
            f.write("lock file")
        with open(new_file, 'w') as f:
            f.write("current package")
        
        # Set old timestamps
        os.utime(old_file1, (old_time, old_time))
        os.utime(old_file2, (old_time, old_time))
        
        manager = StorageManager(self.mock_config_manager)
        result = manager.cleanup_old_syncs(days_old=30)
        
        # Old files matching cleanup patterns should be deleted
        assert not os.path.exists(old_file1)
        assert not os.path.exists(old_file2)
        assert os.path.exists(new_file)  # Current file should remain
        
        assert result['deleted_files'] == 2
        assert result['freed_space'] > 0
        assert len(result['errors']) == 0
    
    def test_should_cleanup_file(self):
        """Test file cleanup pattern matching"""
        manager = StorageManager(self.mock_config_manager)
        
        # Files that should be cleaned up
        assert manager._should_cleanup_file("file.tmp")
        assert manager._should_cleanup_file("cache.lock")
        assert manager._should_cleanup_file("download.partial")
        assert manager._should_cleanup_file("sync.log")
        
        # Files that should not be cleaned up
        assert not manager._should_cleanup_file("package.deb")
        assert not manager._should_cleanup_file("Release.gpg")
        assert not manager._should_cleanup_file("Packages")
    
    def test_is_empty_directory(self):
        """Test empty directory detection"""
        manager = StorageManager(self.mock_config_manager)
        
        # Create empty directory
        empty_dir = os.path.join(self.temp_dir, "empty")
        os.makedirs(empty_dir)
        
        # Create non-empty directory
        nonempty_dir = os.path.join(self.temp_dir, "nonempty")
        os.makedirs(nonempty_dir)
        with open(os.path.join(nonempty_dir, "file.txt"), 'w') as f:
            f.write("content")
        
        assert manager._is_empty_directory(empty_dir) is True
        assert manager._is_empty_directory(nonempty_dir) is False
        assert manager._is_empty_directory("/nonexistent") is False
    
    def test_is_protected_directory(self):
        """Test protected directory detection"""
        manager = StorageManager(self.mock_config_manager)
        
        # Protected directories
        assert manager._is_protected_directory("/path/to/mirror")
        assert manager._is_protected_directory("/path/to/repodata")
        assert manager._is_protected_directory("/path/to/dists")
        assert manager._is_protected_directory("/path/to/pool")
        
        # Non-protected directories
        assert not manager._is_protected_directory("/path/to/temp")
        assert not manager._is_protected_directory("/path/to/cache")
    
    @patch('psutil.disk_usage')
    def test_check_disk_space_sufficient(self, mock_disk_usage):
        """Test disk space check with sufficient space"""
        mock_disk_usage.return_value = Mock(
            free=50 * 1024**3  # 50 GB free
        )
        
        manager = StorageManager(self.mock_config_manager)
        
        with patch('os.path.exists', return_value=True):
            result = manager.check_disk_space(required_gb=10.0)
        
        assert result['sufficient_space'] is True
        assert result['available_gb'] == 50.0
        assert result['required_gb'] == 10.0
    
    @patch('psutil.disk_usage')
    def test_check_disk_space_insufficient(self, mock_disk_usage):
        """Test disk space check with insufficient space"""
        mock_disk_usage.return_value = Mock(
            free=5 * 1024**3  # 5 GB free
        )
        
        manager = StorageManager(self.mock_config_manager)
        
        with patch('os.path.exists', return_value=True):
            result = manager.check_disk_space(required_gb=10.0)
        
        assert result['sufficient_space'] is False
        assert result['available_gb'] == 5.0
    
    def test_create_backup_success(self):
        """Test successful backup creation"""
        # Create source directory with content
        source_dir = os.path.join(self.mock_config.apt_path, "debian", "bookworm")
        os.makedirs(source_dir, exist_ok=True)
        
        source_file = os.path.join(source_dir, "Packages")
        with open(source_file, 'w') as f:
            f.write("Package: test\nVersion: 1.0\n")
        
        manager = StorageManager(self.mock_config_manager)
        
        with patch('src.storage.manager.datetime') as mock_datetime:
            mock_datetime.now.return_value.strftime.return_value = "20240101-120000"
            backup_path = manager.create_backup("debian", "bookworm")
        
        assert backup_path is not None
        assert os.path.exists(backup_path)
        assert os.path.exists(os.path.join(backup_path, "Packages"))
        
        # Verify backup content
        with open(os.path.join(backup_path, "Packages")) as f:
            content = f.read()
            assert "Package: test" in content
    
    def test_create_backup_source_not_exists(self):
        """Test backup creation when source doesn't exist"""
        manager = StorageManager(self.mock_config_manager)
        
        backup_path = manager.create_backup("debian", "nonexistent")
        
        assert backup_path is None
    
    def test_restore_backup_success(self):
        """Test successful backup restoration"""
        # Create backup directory with content
        backup_dir = os.path.join(self.temp_dir, "backup_test")
        os.makedirs(backup_dir, exist_ok=True)
        
        backup_file = os.path.join(backup_dir, "Packages")
        with open(backup_file, 'w') as f:
            f.write("Backup content")
        
        # Ensure target directory exists and has different content
        target_dir = os.path.join(self.mock_config.apt_path, "debian", "bookworm")
        os.makedirs(target_dir, exist_ok=True)
        target_file = os.path.join(target_dir, "Packages")
        with open(target_file, 'w') as f:
            f.write("Original content")
        
        manager = StorageManager(self.mock_config_manager)
        success = manager.restore_backup(backup_dir, "debian", "bookworm")
        
        assert success is True
        assert os.path.exists(target_file)
        
        # Verify content was restored
        with open(target_file) as f:
            content = f.read()
            assert content == "Backup content"
    
    def test_restore_backup_not_exists(self):
        """Test backup restoration when backup doesn't exist"""
        manager = StorageManager(self.mock_config_manager)
        
        success = manager.restore_backup("/nonexistent/backup", "debian", "bookworm")
        
        assert success is False


@pytest.mark.integration
class TestStorageManagerIntegration:
    """Integration tests for StorageManager with real filesystem operations"""
    
    def test_full_storage_lifecycle(self):
        """Test complete storage lifecycle"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create real config
            config = MirrorConfig(base_path=temp_dir)
            config.distributions = {
                "test-dist": DistributionConfig(
                    name="test-dist",
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
            config_manager.get_distribution_path.return_value = os.path.join(config.apt_path, "test-dist")
            
            manager = StorageManager(config_manager)
            
            # Test directory creation
            results = manager.ensure_directory_structure()
            assert all(results.values())
            
            # Test storage info
            storage_info = manager.get_storage_info()
            assert storage_info['base_path'] == temp_dir
            
            # Test backup and restore
            dist_path = os.path.join(config.apt_path, "test-dist", "stable")
            os.makedirs(dist_path, exist_ok=True)
            
            test_file = os.path.join(dist_path, "test.txt")
            with open(test_file, 'w') as f:
                f.write("test content")
            
            backup_path = manager.create_backup("test-dist", "stable")
            assert backup_path is not None
            assert os.path.exists(backup_path)
            
            # Modify original
            with open(test_file, 'w') as f:
                f.write("modified content")
            
            # Restore backup
            success = manager.restore_backup(backup_path, "test-dist", "stable")
            assert success is True
            
            # Verify restoration
            with open(test_file) as f:
                assert f.read() == "test content"


@pytest.mark.slow
class TestStorageManagerPerformance:
    """Performance tests for StorageManager"""
    
    def test_large_directory_size_calculation(self):
        """Test directory size calculation with many files"""
        pytest.skip("Performance tests require large test data")