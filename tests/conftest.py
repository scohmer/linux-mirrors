#!/usr/bin/env python3

"""
Pytest configuration and shared fixtures for linux-mirrors test suite.
"""

import os
import sys
import tempfile
import pytest
from unittest.mock import Mock
from pathlib import Path

# Add src to Python path for imports
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

from src.config.manager import ConfigManager, DistributionConfig, MirrorConfig


@pytest.fixture
def temp_dir():
    """Provide a temporary directory that gets cleaned up after test"""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    
    # Cleanup
    import shutil
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)


@pytest.fixture
def sample_apt_distribution():
    """Provide a sample APT distribution configuration"""
    return DistributionConfig(
        name="debian",
        type="apt",
        versions=["bookworm", "bullseye"],
        mirror_urls=["http://deb.debian.org/debian/"],
        components=["main", "contrib", "non-free"],
        architectures=["amd64", "arm64"],
        enabled=True,
        sync_schedule="daily",
        include_source_packages=True
    )


@pytest.fixture
def sample_yum_distribution():
    """Provide a sample YUM distribution configuration"""
    return DistributionConfig(
        name="rocky",
        type="yum",
        versions=["8", "9"],
        mirror_urls=["https://download.rockylinux.org/pub/rocky/"],
        architectures=["x86_64", "aarch64"],
        enabled=True,
        sync_schedule="weekly"
    )


@pytest.fixture
def sample_disabled_distribution():
    """Provide a sample disabled distribution configuration"""
    return DistributionConfig(
        name="disabled-dist",
        type="apt",
        versions=["stable"],
        mirror_urls=["http://example.com/"],
        components=["main"],
        architectures=["amd64"],
        enabled=False
    )


@pytest.fixture
def sample_mirror_config(temp_dir):
    """Provide a sample mirror configuration"""
    return MirrorConfig(
        base_path=temp_dir,
        container_runtime="podman",
        max_concurrent_syncs=3,
        log_level="INFO"
    )


@pytest.fixture
def sample_distributions(sample_apt_distribution, sample_yum_distribution, sample_disabled_distribution):
    """Provide a dictionary of sample distributions"""
    return {
        "debian": sample_apt_distribution,
        "rocky": sample_yum_distribution,
        "disabled": sample_disabled_distribution
    }


@pytest.fixture
def mock_config_manager(sample_mirror_config, sample_distributions, temp_dir):
    """Provide a mock ConfigManager with sample data"""
    mock_manager = Mock(spec=ConfigManager)
    
    # Set up the config with distributions
    sample_mirror_config.distributions = sample_distributions
    mock_manager.get_config.return_value = sample_mirror_config
    mock_manager.config_path = os.path.join(temp_dir, "config.yaml")
    
    # Mock distribution path getter
    def get_distribution_path(dist_name):
        dist = sample_distributions[dist_name]
        if dist.type == "apt":
            return os.path.join(sample_mirror_config.apt_path, dist_name)
        else:
            return os.path.join(sample_mirror_config.yum_path, dist_name)
    
    mock_manager.get_distribution_path.side_effect = get_distribution_path
    
    # Mock enabled distributions getter
    def get_enabled_distributions():
        return {name: dist for name, dist in sample_distributions.items() if dist.enabled}
    
    mock_manager.get_enabled_distributions.side_effect = get_enabled_distributions
    
    return mock_manager


@pytest.fixture
def real_config_manager(temp_dir):
    """Provide a real ConfigManager instance for integration tests"""
    config_path = os.path.join(temp_dir, "test_config.yaml")
    manager = ConfigManager(config_path)
    
    # Load default config which will create the file
    manager.load_config()
    
    return manager


@pytest.fixture(autouse=True)
def setup_logging():
    """Set up logging for tests"""
    import logging
    
    # Set up basic logging for tests
    logging.basicConfig(
        level=logging.WARNING,  # Only show warnings and errors in tests
        format="%(name)s - %(levelname)s - %(message)s"
    )
    
    # Silence some noisy loggers during tests
    logging.getLogger("asyncio").setLevel(logging.ERROR)


@pytest.fixture
def mock_subprocess_run():
    """Provide a mock for subprocess.run that can be configured per test"""
    with pytest.mock.patch('subprocess.run') as mock_run:
        # Default successful response
        mock_run.return_value = Mock(
            returncode=0,
            stdout="",
            stderr=""
        )
        yield mock_run


@pytest.fixture
def sample_container_status():
    """Provide sample container status data"""
    return {
        'id': 'container123',
        'name': 'linux-mirror-debian-bookworm',
        'status': 'running',
        'image': 'localhost/linux-mirror-debian:latest',
        'created': '2023-01-01T00:00:00Z',
        'started': '2023-01-01T00:00:01Z',
        'finished': None
    }


@pytest.fixture
def sample_storage_info():
    """Provide sample storage information"""
    return {
        'base_path': '/srv/mirror',
        'total_repos': 5,
        'paths': [
            {
                'path': '/srv/mirror',
                'type': 'base',
                'total_size': 1000 * 1024**3,  # 1TB
                'used_space': 400 * 1024**3,   # 400GB
                'free_space': 600 * 1024**3,   # 600GB
                'used_percent': 40.0,
                'directory_size': 50 * 1024**3,  # 50GB
                'repo_count': 5,
                'last_accessed': 1640995200,  # 2022-01-01
                'last_modified': 1640995200
            }
        ],
        'last_updated': '2023-01-01T00:00:00'
    }


@pytest.fixture
def sample_sync_results():
    """Provide sample sync operation results"""
    return [
        {
            'distribution': 'debian',
            'version': 'bookworm',
            'status': 'completed',
            'container_id': 'container123',
            'logs': 'Sync completed successfully'
        },
        {
            'distribution': 'debian',
            'version': 'bullseye',
            'status': 'completed',
            'container_id': 'container456',
            'logs': 'Sync completed successfully'
        },
        {
            'distribution': 'rocky',
            'version': '9',
            'status': 'failed',
            'container_id': 'container789',
            'error': 'Network timeout',
            'logs': 'Failed to connect to mirror'
        }
    ]


@pytest.fixture
def environment_variables():
    """Provide controlled environment variables for tests"""
    original_environ = os.environ.copy()
    
    # Set test-specific environment variables
    test_environ = {
        'XDG_CONFIG_HOME': '/tmp/.config',
        'HOME': '/tmp',
        'USER': 'testuser'
    }
    
    os.environ.update(test_environ)
    
    yield test_environ
    
    # Restore original environment
    os.environ.clear()
    os.environ.update(original_environ)


def pytest_configure(config):
    """Configure pytest with custom markers"""
    config.addinivalue_line(
        "markers", "unit: marks tests as unit tests"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )
    config.addinivalue_line(
        "markers", "slow: marks tests as slow running"
    )
    config.addinivalue_line(
        "markers", "container: marks tests that require container runtime"
    )


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers based on test names"""
    for item in items:
        # Add integration marker to integration test classes
        if "Integration" in item.cls.__name__ if item.cls else False:
            item.add_marker(pytest.mark.integration)
        
        # Add slow marker to performance/stress tests
        if any(keyword in item.name for keyword in ["performance", "stress", "slow"]):
            item.add_marker(pytest.mark.slow)
        
        # Add container marker to container-related tests  
        if any(keyword in item.name.lower() for keyword in ["container", "orchestrator"]):
            if "container" not in [mark.name for mark in item.iter_markers()]:
                # Only add if not already marked as container test
                if "mock" not in item.name.lower():  # Don't mark mocked container tests
                    item.add_marker(pytest.mark.container)


@pytest.fixture(scope="session")
def test_data_dir():
    """Provide path to test data directory"""
    return Path(__file__).parent / "test_data"


# Custom assertions for testing
class CustomAssertions:
    """Custom assertion helpers for tests"""
    
    @staticmethod
    def assert_valid_container_name(name: str):
        """Assert that a container name is valid"""
        import re
        pattern = r'^linux-mirror-[a-z0-9-]+-[a-z0-9.-]+$'
        assert re.match(pattern, name), f"Invalid container name: {name}"
    
    @staticmethod
    def assert_valid_service_name(name: str):
        """Assert that a systemd service name is valid"""
        import re
        pattern = r'^[a-zA-Z0-9_.-]+$'
        assert re.match(pattern, name), f"Invalid service name: {name}"
        assert name.endswith('.service') or '.' not in name, f"Service name should not have extension or be .service: {name}"
    
    @staticmethod
    def assert_directory_exists_with_permissions(path: str, expected_mode: int = 0o755):
        """Assert directory exists with correct permissions"""
        assert os.path.exists(path), f"Directory does not exist: {path}"
        assert os.path.isdir(path), f"Path is not a directory: {path}"
        
        actual_mode = os.stat(path).st_mode & 0o777
        assert actual_mode == expected_mode, f"Directory {path} has mode {oct(actual_mode)}, expected {oct(expected_mode)}"
    
    @staticmethod
    def assert_file_contains(file_path: str, content: str):
        """Assert that file contains specified content"""
        assert os.path.exists(file_path), f"File does not exist: {file_path}"
        
        with open(file_path, 'r') as f:
            file_content = f.read()
        
        assert content in file_content, f"File {file_path} does not contain: {content}"


@pytest.fixture
def assert_helpers():
    """Provide custom assertion helpers"""
    return CustomAssertions()