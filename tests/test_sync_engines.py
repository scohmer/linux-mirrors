#!/usr/bin/env python3

import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock, call
from typing import Dict, Any

from src.config.manager import DistributionConfig
from src.containers.orchestrator import ContainerOrchestrator
from src.sync.engines import SyncEngine, AptSyncEngine, YumSyncEngine, SyncManager


class MockSyncEngine(SyncEngine):
    """Mock implementation of SyncEngine for testing"""
    
    def generate_sync_command(self, version: str) -> list[str]:
        return ['echo', f'syncing {self.dist_config.name} {version}']
    
    def validate_config(self) -> bool:
        return True


class TestSyncEngine:
    """Test base SyncEngine functionality"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.dist_config = DistributionConfig(
            name="test-dist",
            type="apt",
            versions=["stable"],
            mirror_urls=["http://example.com/"],
            enabled=True
        )
        
        self.mock_orchestrator = Mock(spec=ContainerOrchestrator)
    
    def test_sync_engine_init(self):
        """Test SyncEngine initialization"""
        engine = MockSyncEngine(self.dist_config, self.mock_orchestrator)
        
        assert engine.dist_config == self.dist_config
        assert engine.orchestrator == self.mock_orchestrator
        assert engine.container_id is None
    
    @pytest.mark.asyncio
    async def test_sync_version_success(self):
        """Test successful sync_version execution"""
        engine = MockSyncEngine(self.dist_config, self.mock_orchestrator)
        
        # Mock orchestrator methods
        self.mock_orchestrator.create_sync_container.return_value = "container123"
        self.mock_orchestrator.start_sync_container.return_value = None
        self.mock_orchestrator.get_container_status.side_effect = [
            {'status': 'running'},  # First check
            {'status': 'exited'}    # Second check (completed)
        ]
        self.mock_orchestrator.get_container_logs.return_value = "Sync completed successfully"
        
        # Mock the monitoring sleep
        with patch('asyncio.sleep', new_callable=AsyncMock):
            result = await engine.sync_version("stable")
        
        assert result['distribution'] == "test-dist"
        assert result['version'] == "stable"
        assert result['status'] == 'completed'
        assert result['container_id'] == "container123"
        assert 'logs' in result
    
    @pytest.mark.asyncio
    async def test_sync_version_container_failed(self):
        """Test sync_version when container fails"""
        engine = MockSyncEngine(self.dist_config, self.mock_orchestrator)
        
        self.mock_orchestrator.create_sync_container.return_value = "container123"
        self.mock_orchestrator.start_sync_container.return_value = None
        self.mock_orchestrator.get_container_status.return_value = {
            'status': 'dead'
        }
        self.mock_orchestrator.get_container_logs.return_value = "Container died"
        
        with patch('asyncio.sleep', new_callable=AsyncMock):
            result = await engine.sync_version("stable")
        
        assert result['status'] == 'failed'
        assert result['container_id'] == "container123"
    
    @pytest.mark.asyncio
    async def test_sync_version_container_not_found(self):
        """Test sync_version when container is removed externally"""
        engine = MockSyncEngine(self.dist_config, self.mock_orchestrator)
        
        self.mock_orchestrator.create_sync_container.return_value = "container123"
        self.mock_orchestrator.start_sync_container.return_value = None
        self.mock_orchestrator.get_container_status.return_value = {
            'status': 'not found',
            'error': 'Container not found'
        }
        
        with patch('asyncio.sleep', new_callable=AsyncMock):
            result = await engine.sync_version("stable")
        
        assert result['status'] == 'failed'
        assert 'removed before sync completed' in result['logs']
    
    @pytest.mark.asyncio
    async def test_sync_version_validation_failure(self):
        """Test sync_version when configuration validation fails"""
        class InvalidSyncEngine(MockSyncEngine):
            def validate_config(self) -> bool:
                return False
        
        engine = InvalidSyncEngine(self.dist_config, self.mock_orchestrator)
        
        result = await engine.sync_version("stable")
        
        assert result['status'] == 'failed'
        assert 'Invalid configuration' in result['error']
        assert result['container_id'] is None
    
    @pytest.mark.asyncio
    async def test_sync_version_container_creation_failure(self):
        """Test sync_version when container creation fails"""
        engine = MockSyncEngine(self.dist_config, self.mock_orchestrator)
        
        self.mock_orchestrator.create_sync_container.side_effect = RuntimeError("Creation failed")
        
        result = await engine.sync_version("stable")
        
        assert result['status'] == 'failed'
        assert 'Creation failed' in result['error']


class TestAptSyncEngine:
    """Test APT-specific sync engine functionality"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.dist_config = DistributionConfig(
            name="debian",
            type="apt",
            versions=["bookworm", "bullseye"],
            mirror_urls=["http://deb.debian.org/debian/"],
            components=["main", "contrib", "non-free"],
            architectures=["amd64", "arm64"],
            include_source_packages=True
        )
        
        self.mock_orchestrator = Mock(spec=ContainerOrchestrator)
    
    def test_apt_sync_engine_init(self):
        """Test APT sync engine initialization"""
        engine = AptSyncEngine(self.dist_config, self.mock_orchestrator)
        
        assert engine.dist_config == self.dist_config
        assert engine.orchestrator == self.mock_orchestrator
    
    def test_validate_config_valid(self):
        """Test configuration validation with valid APT config"""
        engine = AptSyncEngine(self.dist_config, self.mock_orchestrator)
        
        assert engine.validate_config() is True
    
    def test_validate_config_missing_mirror_urls(self):
        """Test configuration validation with missing mirror URLs"""
        invalid_config = DistributionConfig(
            name="debian",
            type="apt",
            versions=["bookworm"],
            mirror_urls=[],  # Empty
            components=["main"],
            architectures=["amd64"]
        )
        
        engine = AptSyncEngine(invalid_config, self.mock_orchestrator)
        
        assert engine.validate_config() is False
    
    def test_validate_config_missing_components(self):
        """Test configuration validation with missing components"""
        invalid_config = DistributionConfig(
            name="debian",
            type="apt",
            versions=["bookworm"],
            mirror_urls=["http://deb.debian.org/debian/"],
            components=None,  # Missing
            architectures=["amd64"]
        )
        
        engine = AptSyncEngine(invalid_config, self.mock_orchestrator)
        
        assert engine.validate_config() is False
    
    def test_generate_sync_command(self):
        """Test APT sync command generation"""
        engine = AptSyncEngine(self.dist_config, self.mock_orchestrator)
        
        command = engine.generate_sync_command("bookworm")
        
        assert command[0] == 'sh'
        assert command[1] == '-c'
        
        # Check that the command contains apt-mirror execution
        script = command[2]
        assert 'apt-mirror /mirror/apt-mirror.list' in script
        assert 'echo' in script  # Config creation
    
    def test_generate_apt_mirror_config(self):
        """Test APT mirror configuration generation"""
        engine = AptSyncEngine(self.dist_config, self.mock_orchestrator)
        
        config = engine._generate_apt_mirror_config("bookworm")
        
        # Check basic configuration
        assert "set base_path /mirror" in config
        assert "set defaultarch amd64 arm64" in config
        
        # Check repository lines
        assert "deb-amd64 http://deb.debian.org/debian/ bookworm main contrib non-free" in config
        assert "deb-arm64 http://deb.debian.org/debian/ bookworm main contrib non-free" in config
        
        # Check source packages (enabled in test config)
        assert "deb-src http://deb.debian.org/debian/ bookworm main contrib non-free" in config
    
    def test_generate_apt_mirror_config_no_sources(self):
        """Test APT mirror config generation without source packages"""
        config_no_sources = DistributionConfig(
            name="debian",
            type="apt",
            versions=["bookworm"],
            mirror_urls=["http://deb.debian.org/debian/"],
            components=["main"],
            architectures=["amd64"],
            include_source_packages=False
        )
        
        engine = AptSyncEngine(config_no_sources, self.mock_orchestrator)
        config = engine._generate_apt_mirror_config("bookworm")
        
        # Should not contain source lines
        assert "deb-src" not in config
        assert "deb-amd64" in config


class TestYumSyncEngine:
    """Test YUM-specific sync engine functionality"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.dist_config = DistributionConfig(
            name="rocky",
            type="yum",
            versions=["9"],
            mirror_urls=["https://download.rockylinux.org/pub/rocky/"],
            architectures=["x86_64", "aarch64"]
        )
        
        self.mock_orchestrator = Mock(spec=ContainerOrchestrator)
    
    def test_yum_sync_engine_init(self):
        """Test YUM sync engine initialization"""
        engine = YumSyncEngine(self.dist_config, self.mock_orchestrator)
        
        assert engine.dist_config == self.dist_config
        assert engine.orchestrator == self.mock_orchestrator
    
    def test_validate_config_valid(self):
        """Test configuration validation with valid YUM config"""
        engine = YumSyncEngine(self.dist_config, self.mock_orchestrator)
        
        assert engine.validate_config() is True
    
    def test_validate_config_missing_architectures(self):
        """Test configuration validation with missing architectures"""
        invalid_config = DistributionConfig(
            name="rocky",
            type="yum",
            versions=["9"],
            mirror_urls=["https://download.rockylinux.org/pub/rocky/"],
            architectures=[]  # Empty
        )
        
        engine = YumSyncEngine(invalid_config, self.mock_orchestrator)
        
        assert engine.validate_config() is False
    
    def test_generate_sync_command(self):
        """Test YUM sync command generation"""
        engine = YumSyncEngine(self.dist_config, self.mock_orchestrator)
        
        command = engine.generate_sync_command("9")
        
        assert command[0] == 'sh'
        assert command[1] == '-c'
        
        script = command[2]
        assert 'dnf reposync' in script
        assert 'createrepo_c' in script
        assert '--config=/mirror/rocky-9.repo' in script
    
    def test_generate_yum_repo_config(self):
        """Test YUM repository configuration generation"""
        engine = YumSyncEngine(self.dist_config, self.mock_orchestrator)
        
        config = engine._generate_yum_repo_config("9")
        
        # Check BaseOS repositories
        assert "[rocky-9-baseos-x86_64]" in config
        assert "[rocky-9-baseos-aarch64]" in config
        
        # Check AppStream repositories  
        assert "[rocky-9-appstream-x86_64]" in config
        assert "[rocky-9-appstream-aarch64]" in config
        
        # Check baseurl patterns
        assert "baseurl=https://download.rockylinux.org/pub/rocky/9/BaseOS/x86_64/os/" in config
        assert "baseurl=https://download.rockylinux.org/pub/rocky/9/AppStream/aarch64/os/" in config
        
        # Check that GPG check is disabled for simplicity
        assert "gpgcheck=0" in config


class TestSyncManager:
    """Test SyncManager functionality"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_orchestrator = Mock(spec=ContainerOrchestrator)
        
        # Mock config manager in orchestrator
        self.mock_config_manager = Mock()
        self.mock_config = Mock()
        self.mock_orchestrator.config_manager = self.mock_config_manager
        self.mock_config_manager.get_config.return_value = self.mock_config
        
        self.apt_dist = DistributionConfig(
            name="debian",
            type="apt", 
            versions=["bookworm", "bullseye"],
            mirror_urls=["http://deb.debian.org/debian/"],
            components=["main"],
            architectures=["amd64"],
            enabled=True
        )
        
        self.yum_dist = DistributionConfig(
            name="rocky",
            type="yum",
            versions=["9"],
            mirror_urls=["https://download.rockylinux.org/pub/rocky/"],
            architectures=["x86_64"],
            enabled=True
        )
        
        self.disabled_dist = DistributionConfig(
            name="disabled",
            type="apt",
            versions=["stable"],
            mirror_urls=["http://example.com/"],
            components=["main"],
            architectures=["amd64"],
            enabled=False
        )
        
        self.mock_config.distributions = {
            "debian": self.apt_dist,
            "rocky": self.yum_dist,
            "disabled": self.disabled_dist
        }
    
    def test_sync_manager_init(self):
        """Test SyncManager initialization"""
        manager = SyncManager(self.mock_orchestrator)
        
        assert manager.orchestrator == self.mock_orchestrator
        assert manager._engines == {}
        assert manager._apt_semaphore._value == 1  # APT limited to 1 concurrent
        assert manager._yum_semaphore._value == 3  # YUM allows 3 concurrent
    
    def test_get_engine_apt(self):
        """Test getting APT sync engine"""
        manager = SyncManager(self.mock_orchestrator)
        
        engine = manager.get_engine(self.apt_dist)
        
        assert isinstance(engine, AptSyncEngine)
        assert engine.dist_config == self.apt_dist
        
        # Test caching - should return same instance
        engine2 = manager.get_engine(self.apt_dist)
        assert engine is engine2
    
    def test_get_engine_yum(self):
        """Test getting YUM sync engine"""
        manager = SyncManager(self.mock_orchestrator)
        
        engine = manager.get_engine(self.yum_dist)
        
        assert isinstance(engine, YumSyncEngine)
        assert engine.dist_config == self.yum_dist
    
    def test_get_engine_unknown_type(self):
        """Test getting engine for unknown repository type"""
        unknown_dist = DistributionConfig(
            name="unknown",
            type="unknown",
            versions=["1"],
            mirror_urls=["http://example.com/"]
        )
        
        manager = SyncManager(self.mock_orchestrator)
        
        with pytest.raises(ValueError, match="Unknown repository type: unknown"):
            manager.get_engine(unknown_dist)
    
    @pytest.mark.asyncio
    async def test_sync_distribution_disabled(self):
        """Test syncing disabled distribution returns empty list"""
        manager = SyncManager(self.mock_orchestrator)
        
        results = await manager.sync_distribution(self.disabled_dist)
        
        assert results == []
    
    @pytest.mark.asyncio 
    async def test_sync_distribution_apt_sequential(self):
        """Test APT distribution sync runs sequentially"""
        manager = SyncManager(self.mock_orchestrator)
        
        # Mock the engine and its sync_version method
        mock_engine = Mock(spec=AptSyncEngine)
        mock_engine.sync_version = AsyncMock(side_effect=[
            {'distribution': 'debian', 'version': 'bookworm', 'status': 'completed'},
            {'distribution': 'debian', 'version': 'bullseye', 'status': 'completed'}
        ])
        
        with patch.object(manager, 'get_engine', return_value=mock_engine):
            results = await manager.sync_distribution(self.apt_dist)
        
        assert len(results) == 2
        assert results[0]['version'] == 'bookworm'
        assert results[1]['version'] == 'bullseye'
        
        # Verify sync_version was called for each version
        assert mock_engine.sync_version.call_count == 2
    
    @pytest.mark.asyncio
    async def test_sync_distribution_yum_parallel(self):
        """Test YUM distribution sync runs in parallel"""
        manager = SyncManager(self.mock_orchestrator)
        
        # Mock the engine
        mock_engine = Mock(spec=YumSyncEngine)
        mock_engine.sync_version = AsyncMock(return_value={
            'distribution': 'rocky', 'version': '9', 'status': 'completed'
        })
        
        with patch.object(manager, 'get_engine', return_value=mock_engine):
            results = await manager.sync_distribution(self.yum_dist)
        
        assert len(results) == 1
        assert results[0]['distribution'] == 'rocky'
        assert results[0]['version'] == '9'
        assert results[0]['status'] == 'completed'
    
    @pytest.mark.asyncio
    async def test_sync_distribution_with_specific_versions(self):
        """Test syncing specific versions only"""
        manager = SyncManager(self.mock_orchestrator)
        
        mock_engine = Mock(spec=AptSyncEngine)
        mock_engine.sync_version = AsyncMock(return_value={
            'distribution': 'debian', 'version': 'bookworm', 'status': 'completed'
        })
        
        with patch.object(manager, 'get_engine', return_value=mock_engine):
            results = await manager.sync_distribution(self.apt_dist, versions=["bookworm"])
        
        assert len(results) == 1
        assert results[0]['version'] == 'bookworm'
        
        # Should only be called once for the specific version
        assert mock_engine.sync_version.call_count == 1
        mock_engine.sync_version.assert_called_once_with("bookworm")
    
    @pytest.mark.asyncio
    async def test_sync_distribution_with_exception(self):
        """Test handling exceptions during distribution sync"""
        manager = SyncManager(self.mock_orchestrator)
        
        mock_engine = Mock(spec=AptSyncEngine)
        mock_engine.sync_version = AsyncMock(side_effect=[
            {'distribution': 'debian', 'version': 'bookworm', 'status': 'completed'},
            Exception("Sync failed")
        ])
        
        with patch.object(manager, 'get_engine', return_value=mock_engine):
            results = await manager.sync_distribution(self.apt_dist)
        
        assert len(results) == 2
        assert results[0]['status'] == 'completed'
        assert results[1]['status'] == 'failed'
        assert 'Sync failed' in results[1]['error']
    
    @pytest.mark.asyncio
    async def test_sync_multiple_distributions_strategy(self):
        """Test sync strategy for multiple distributions (APT sequential, YUM parallel)"""
        manager = SyncManager(self.mock_orchestrator)
        
        distributions = {
            "debian": ["bookworm"],
            "rocky": ["9"]
        }
        
        # Mock distribution sync method
        apt_result = [{'distribution': 'debian', 'version': 'bookworm', 'status': 'completed'}]
        yum_result = [{'distribution': 'rocky', 'version': '9', 'status': 'completed'}]
        
        with patch.object(manager, 'sync_distribution', side_effect=[apt_result, yum_result]) as mock_sync:
            results = await manager.sync_multiple_distributions(distributions)
        
        assert len(results) == 2
        assert any(r['distribution'] == 'debian' for r in results)
        assert any(r['distribution'] == 'rocky' for r in results)
        
        # Verify sync_distribution was called for each distribution
        assert mock_sync.call_count == 2
    
    @pytest.mark.asyncio
    async def test_sync_multiple_distributions_disabled_filtered(self):
        """Test that disabled distributions are filtered out"""
        manager = SyncManager(self.mock_orchestrator)
        
        distributions = {
            "debian": ["bookworm"], 
            "disabled": ["stable"]  # This distribution is disabled
        }
        
        with patch.object(manager, 'sync_distribution', return_value=[]) as mock_sync:
            results = await manager.sync_multiple_distributions(distributions)
        
        # Should only sync enabled distributions
        assert mock_sync.call_count == 1
        mock_sync.assert_called_once_with(self.apt_dist, ["bookworm"])


@pytest.mark.integration
class TestSyncEngineIntegration:
    """Integration tests for sync engines"""
    
    @pytest.mark.asyncio
    async def test_apt_sync_engine_full_workflow(self):
        """Test complete APT sync workflow"""
        # This would test actual sync engine with mocked container operations
        dist_config = DistributionConfig(
            name="test-debian",
            type="apt",
            versions=["bookworm"],
            mirror_urls=["http://deb.debian.org/debian/"],
            components=["main"],
            architectures=["amd64"]
        )
        
        mock_orchestrator = Mock()
        mock_orchestrator.create_sync_container.return_value = "test-container"
        mock_orchestrator.start_sync_container.return_value = None
        mock_orchestrator.get_container_status.side_effect = [
            {'status': 'running'},
            {'status': 'exited'}
        ]
        mock_orchestrator.get_container_logs.return_value = "Sync completed"
        
        engine = AptSyncEngine(dist_config, mock_orchestrator)
        
        with patch('asyncio.sleep', new_callable=AsyncMock):
            result = await engine.sync_version("bookworm")
        
        assert result['status'] == 'completed'
        assert result['distribution'] == 'test-debian'
        assert result['version'] == 'bookworm'
        
        # Verify container operations were called
        mock_orchestrator.create_sync_container.assert_called_once()
        mock_orchestrator.start_sync_container.assert_called_once_with("test-container")


@pytest.mark.slow
class TestSyncManagerStressTests:
    """Stress tests for sync manager"""
    
    @pytest.mark.asyncio
    async def test_concurrent_sync_limits(self):
        """Test that concurrent sync limits are respected"""
        # This would test the semaphore limits for concurrent operations
        pytest.skip("Stress tests require extensive setup")