#!/usr/bin/env python3

import pytest
import os
import tempfile
import subprocess
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

from src.verification.checker import RepositoryVerifier
from src.config.manager import ConfigManager, DistributionConfig


class TestRepositoryVerifier:
    """Test RepositoryVerifier file integrity verification"""
    
    @pytest.fixture
    def mock_config_manager(self):
        """Create mock configuration manager"""
        config_manager = Mock(spec=ConfigManager)
        config_manager.config = Mock()
        config_manager.config.base_path = "/srv/mirror"
        config_manager.config.apt_path = "/srv/mirror/apt"
        config_manager.config.yum_path = "/srv/mirror/yum"
        config_manager.config.distributions = {
            "debian": DistributionConfig(
                name="debian",
                type="apt",
                versions=["bookworm"],
                mirror_urls=["http://deb.debian.org/debian"],
                components=["main"],
                architectures=["amd64"],
                enabled=True
            ),
            "rocky": DistributionConfig(
                name="rocky",
                type="yum",
                versions=["9"],
                mirror_urls=["http://dl.rockylinux.org/pub/rocky"],
                components=["BaseOS", "AppStream"],
                architectures=["x86_64"],
                enabled=True
            )
        }
        return config_manager
    
    def test_init(self, mock_config_manager):
        """Test RepositoryVerifier initialization"""
        verifier = RepositoryVerifier(mock_config_manager)
        assert verifier.config_manager == mock_config_manager
        assert verifier.config == mock_config_manager.config
    
    @patch('os.path.exists')
    def test_verify_file_integrity_missing_repo(self, mock_exists, mock_config_manager):
        """Test file integrity verification when repository is missing"""
        mock_exists.return_value = False
        
        verifier = RepositoryVerifier(mock_config_manager)
        dist_config = mock_config_manager.config.distributions["debian"]
        
        result = verifier.verify_file_integrity("debian", "bookworm", dist_config)
        
        assert result['status'] == 'missing'
        assert result['distribution'] == 'debian'
        assert result['version'] == 'bookworm'
        assert result['gpg_verified'] == False
        assert result['checksums_verified'] == 0
        assert 'not found' in result['details']
    
    @patch('os.path.exists')
    @patch('src.verification.checker.RepositoryVerifier._verify_apt_file_integrity')
    def test_verify_apt_file_integrity(self, mock_verify_apt, mock_exists, mock_config_manager):
        """Test APT file integrity verification"""
        mock_exists.return_value = True
        mock_verify_apt.return_value = {
            'distribution': 'debian',
            'version': 'bookworm',
            'status': 'verified',
            'path': '/srv/mirror/apt/debian',
            'details': 'File integrity verified',
            'gpg_verified': True,
            'checksums_verified': 15,
            'total_files_checked': 15
        }
        
        verifier = RepositoryVerifier(mock_config_manager)
        dist_config = mock_config_manager.config.distributions["debian"]
        
        result = verifier.verify_file_integrity("debian", "bookworm", dist_config)
        
        assert result['status'] == 'verified'
        assert result['gpg_verified'] == True
        assert result['checksums_verified'] == 15
        assert result['total_files_checked'] == 15
        mock_verify_apt.assert_called_once()
    
    @patch('subprocess.run')
    def test_verify_gpg_file_success(self, mock_subprocess, mock_config_manager):
        """Test successful GPG file verification"""
        mock_subprocess.return_value = Mock(returncode=0, stderr="")
        
        verifier = RepositoryVerifier(mock_config_manager)
        result = verifier._verify_gpg_file("/path/to/InRelease", "test file")
        
        assert result['verified'] == True
        assert 'GPG signature verified' in result['details']
        mock_subprocess.assert_called_once_with(
            ['gpg', '--verify', '/path/to/InRelease'],
            capture_output=True, text=True, timeout=30
        )
    
    @patch('subprocess.run')
    def test_verify_gpg_file_failure(self, mock_subprocess, mock_config_manager):
        """Test failed GPG file verification"""
        mock_subprocess.return_value = Mock(returncode=1, stderr="Bad signature")
        
        verifier = RepositoryVerifier(mock_config_manager)
        result = verifier._verify_gpg_file("/path/to/InRelease", "test file")
        
        assert result['verified'] == False
        assert 'GPG verification failed' in result['details']
        assert 'Bad signature' in result['details']
    
    @patch('subprocess.run')
    def test_verify_gpg_detached_success(self, mock_subprocess, mock_config_manager):
        """Test successful detached GPG signature verification"""
        mock_subprocess.return_value = Mock(returncode=0, stderr="")
        
        verifier = RepositoryVerifier(mock_config_manager)
        result = verifier._verify_gpg_detached("/path/to/Release", "/path/to/Release.gpg", "test file")
        
        assert result['verified'] == True
        assert 'GPG signature verified' in result['details']
        mock_subprocess.assert_called_once_with(
            ['gpg', '--verify', '/path/to/Release.gpg', '/path/to/Release'],
            capture_output=True, text=True, timeout=30
        )
    
    def test_calculate_sha256(self, mock_config_manager):
        """Test SHA256 hash calculation"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write("test content")
            temp_file = f.name
        
        try:
            verifier = RepositoryVerifier(mock_config_manager)
            result = verifier._calculate_sha256(temp_file)
            
            # Expected SHA256 of "test content"
            expected = "1eebdf4fdc9fc7bf283031b93f9aef3338de9052580f66202b7ade6317c99093"
            assert result == expected
        finally:
            os.unlink(temp_file)
    
    @patch('os.path.exists')
    def test_verify_apt_checksums_missing_release(self, mock_exists, mock_config_manager):
        """Test APT checksum verification when Release file is missing"""
        mock_exists.return_value = False
        
        verifier = RepositoryVerifier(mock_config_manager)
        dist_config = mock_config_manager.config.distributions["debian"]
        
        result = verifier._verify_apt_checksums("/dists/bookworm", "/repo", dist_config)
        
        assert result['verified_count'] == 0
        assert result['total_count'] == 0
        assert 'No Release file found' in result['details'][0]
    
    def test_verify_apt_checksums_with_release_file(self, mock_config_manager):
        """Test APT checksum verification with actual Release file"""
        with tempfile.TemporaryDirectory() as temp_dir:
            dists_dir = os.path.join(temp_dir, "dists", "bookworm")
            os.makedirs(dists_dir)
            
            # Create mock Release file with SHA256 section
            release_file = os.path.join(dists_dir, "Release")
            with open(release_file, 'w') as f:
                f.write("""SHA256:
 1eebdf4fdc9fc7bf283031b93f9aef3338de9052580f66202b7ade6317c99093 12 main/binary-amd64/Packages
 abcd1234567890abcd1234567890abcd1234567890abcd1234567890abcd1234 25 main/binary-amd64/Release
""")
            
            # Create the first file with matching content
            packages_dir = os.path.join(dists_dir, "main", "binary-amd64")
            os.makedirs(packages_dir)
            packages_file = os.path.join(packages_dir, "Packages")
            with open(packages_file, 'w') as f:
                f.write("test content")
            
            verifier = RepositoryVerifier(mock_config_manager)
            dist_config = mock_config_manager.config.distributions["debian"]
            
            result = verifier._verify_apt_checksums(dists_dir, temp_dir, dist_config)
            
            assert result['verified_count'] == 1  # One file matched
            assert result['total_count'] == 2  # Two files listed
            assert len(result['details']) == 1  # One missing file
            assert 'Missing file' in result['details'][0]
    
    def test_verify_all_repositories_integrity(self, mock_config_manager):
        """Test verifying all repositories with file integrity"""
        verifier = RepositoryVerifier(mock_config_manager)
        
        with patch.object(verifier, 'verify_file_integrity') as mock_verify:
            mock_verify.side_effect = [
                {
                    'distribution': 'debian',
                    'version': 'bookworm',
                    'status': 'verified',
                    'gpg_verified': True,
                    'checksums_verified': 10,
                    'total_files_checked': 10
                },
                {
                    'distribution': 'rocky',
                    'version': '9',
                    'status': 'failed',
                    'gpg_verified': False,
                    'checksums_verified': 5,
                    'total_files_checked': 8
                }
            ]
            
            results = verifier.verify_all_repositories_integrity()
            
            assert results['total_repos'] == 2
            assert results['verified'] == 1
            assert results['failed'] == 1
            assert results['missing'] == 0
            assert results['gpg_verified'] == 1
            assert results['total_checksums_verified'] == 15
            assert results['total_files_checked'] == 18
    
    @patch('os.path.exists')
    def test_apt_gpg_signature_inrelease(self, mock_exists, mock_config_manager):
        """Test APT GPG signature verification with InRelease file"""
        def exists_side_effect(path):
            return 'InRelease' in path
        
        mock_exists.side_effect = exists_side_effect
        
        verifier = RepositoryVerifier(mock_config_manager)
        
        with patch.object(verifier, '_verify_gpg_file') as mock_verify_gpg:
            mock_verify_gpg.return_value = {
                'verified': True,
                'details': 'GPG signature verified'
            }
            
            result = verifier._verify_apt_gpg_signature("/dists/bookworm", "debian", "bookworm")
            
            assert result['verified'] == True
            mock_verify_gpg.assert_called_once()
    
    @patch('os.path.exists')
    def test_apt_gpg_signature_detached(self, mock_exists, mock_config_manager):
        """Test APT GPG signature verification with detached signature"""
        def exists_side_effect(path):
            return 'Release.gpg' in path or path.endswith('Release')
        
        mock_exists.side_effect = exists_side_effect
        
        verifier = RepositoryVerifier(mock_config_manager)
        
        with patch.object(verifier, '_verify_gpg_detached') as mock_verify_gpg:
            mock_verify_gpg.return_value = {
                'verified': True,
                'details': 'GPG signature verified'
            }
            
            result = verifier._verify_apt_gpg_signature("/dists/bookworm", "debian", "bookworm")
            
            assert result['verified'] == True
            mock_verify_gpg.assert_called_once()
    
    @patch('os.path.exists')
    def test_apt_gpg_signature_missing(self, mock_exists, mock_config_manager):
        """Test APT GPG signature verification when no signature files exist"""
        mock_exists.return_value = False
        
        verifier = RepositoryVerifier(mock_config_manager)
        result = verifier._verify_apt_gpg_signature("/dists/bookworm", "debian", "bookworm")
        
        assert result['verified'] == False
        assert 'No GPG signature files found' in result['details']


class TestFileIntegrityIntegration:
    """Integration tests for file integrity verification"""
    
    @patch('subprocess.run')
    @patch('os.path.exists')
    def test_file_integrity_verification_workflow(self, mock_exists, mock_subprocess):
        """Test complete file integrity verification workflow"""
        # Setup mock file system
        def exists_side_effect(path):
            return any(check in path for check in [
                '/srv/mirror/apt/debian',
                'dists/bookworm',
                'InRelease',
                'Release'
            ])
        
        mock_exists.side_effect = exists_side_effect
        
        # Mock GPG verification success
        mock_subprocess.return_value = Mock(returncode=0, stderr="")
        
        # Create config manager
        config_manager = Mock(spec=ConfigManager)
        config_manager.config = Mock()
        config_manager.config.base_path = "/srv/mirror"
        config_manager.config.apt_path = "/srv/mirror/apt"
        config_manager.config.yum_path = "/srv/mirror/yum"
        config_manager.config.distributions = {
            "debian": DistributionConfig(
                name="debian",
                type="apt",
                versions=["bookworm"],
                mirror_urls=["http://deb.debian.org/debian"],
                components=["main"],
                architectures=["amd64"],
                enabled=True
            )
        }
        
        verifier = RepositoryVerifier(config_manager)
        
        # Mock checksum verification
        with patch.object(verifier, '_verify_apt_checksums') as mock_checksums:
            mock_checksums.return_value = {
                'verified_count': 5,
                'total_count': 5,
                'details': []
            }
            
            results = verifier.verify_all_repositories_integrity(check_signatures=True)
            
            assert results['total_repos'] == 1
            assert results['verified'] == 1
            assert results['gpg_verified'] == 1
            assert results['total_checksums_verified'] == 5
            assert results['total_files_checked'] == 5