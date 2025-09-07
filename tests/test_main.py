#!/usr/bin/env python3

import os
import sys
import tempfile
import pytest
import argparse
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from io import StringIO

# Add src to path for testing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.main import (
    setup_logging, create_argument_parser, cmd_sync, cmd_setup_systemd,
    cmd_status, cmd_storage, main
)
from src.config.manager import ConfigManager, DistributionConfig, MirrorConfig


class TestLoggingSetup:
    """Test logging configuration"""
    
    @patch('os.geteuid', return_value=0)
    @patch('logging.basicConfig')
    def test_setup_logging_as_root(self, mock_basicConfig, mock_geteuid):
        """Test logging setup when running as root"""
        setup_logging("DEBUG")
        
        mock_basicConfig.assert_called_once()
        call_args = mock_basicConfig.call_args
        
        # Check log level
        assert call_args[1]['level'] == 10  # DEBUG level
        
        # Check that it tries to log to system location
        handlers = call_args[1]['handlers']
        file_handler = next(h for h in handlers if hasattr(h, 'baseFilename'))
        assert "/var/log/linux-mirrors.log" in file_handler.baseFilename
    
    @patch('os.geteuid', return_value=1000)
    @patch('os.makedirs')
    @patch('logging.basicConfig')
    def test_setup_logging_as_user(self, mock_basicConfig, mock_makedirs, mock_geteuid):
        """Test logging setup when running as regular user"""
        with patch('os.path.expanduser', return_value="/home/user"):
            setup_logging("INFO")
        
        # Should create user log directory
        mock_makedirs.assert_called_once_with(
            "/home/user/.local/log", exist_ok=True
        )
        
        mock_basicConfig.assert_called_once()
        call_args = mock_basicConfig.call_args
        
        # Check that it logs to user location
        handlers = call_args[1]['handlers']
        file_handler = next(h for h in handlers if hasattr(h, 'baseFilename'))
        assert "/home/user/.local/log/linux-mirrors.log" in file_handler.baseFilename
    
    @patch('logging.basicConfig')
    def test_setup_logging_levels(self, mock_basicConfig):
        """Test different logging levels"""
        levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
        expected_levels = [10, 20, 30, 40]
        
        for level_name, expected_level in zip(levels, expected_levels):
            mock_basicConfig.reset_mock()
            setup_logging(level_name)
            
            call_args = mock_basicConfig.call_args
            assert call_args[1]['level'] == expected_level


class TestArgumentParser:
    """Test command-line argument parsing"""
    
    def test_create_argument_parser(self):
        """Test argument parser creation"""
        parser = create_argument_parser()
        
        assert isinstance(parser, argparse.ArgumentParser)
        assert "Linux Repository Mirror Manager" in parser.description
    
    def test_parser_basic_arguments(self):
        """Test basic argument parsing"""
        parser = create_argument_parser()
        
        # Test default arguments
        args = parser.parse_args([])
        assert args.config is None
        assert args.log_level == "INFO"
        assert args.non_interactive is False
        assert args.command is None
    
    def test_parser_config_argument(self):
        """Test config file argument"""
        parser = create_argument_parser()
        
        args = parser.parse_args(["--config", "/path/to/config.yaml"])
        assert args.config == "/path/to/config.yaml"
        
        args = parser.parse_args(["-c", "/other/config.yaml"])
        assert args.config == "/other/config.yaml"
    
    def test_parser_log_level_argument(self):
        """Test log level argument"""
        parser = create_argument_parser()
        
        for level in ["DEBUG", "INFO", "WARNING", "ERROR"]:
            args = parser.parse_args(["--log-level", level])
            assert args.log_level == level
        
        # Test short form
        args = parser.parse_args(["-l", "DEBUG"])
        assert args.log_level == "DEBUG"
    
    def test_parser_non_interactive_argument(self):
        """Test non-interactive argument"""
        parser = create_argument_parser()
        
        args = parser.parse_args(["--non-interactive"])
        assert args.non_interactive is True
        
        args = parser.parse_args(["-n"])
        assert args.non_interactive is True
    
    def test_parser_sync_command(self):
        """Test sync command arguments"""
        parser = create_argument_parser()
        
        # Test sync --all
        args = parser.parse_args(["sync", "--all"])
        assert args.command == "sync"
        assert args.all is True
        
        # Test sync specific distribution
        args = parser.parse_args(["sync", "--distribution", "debian", "--version", "bookworm"])
        assert args.command == "sync"
        assert args.distribution == "debian"
        assert args.version == "bookworm"
        
        # Test short forms
        args = parser.parse_args(["sync", "-d", "ubuntu", "-v", "focal"])
        assert args.distribution == "ubuntu"
        assert args.version == "focal"
    
    def test_parser_systemd_command(self):
        """Test systemd command arguments"""
        parser = create_argument_parser()
        
        args = parser.parse_args(["setup-systemd", "--user"])
        assert args.command == "setup-systemd"
        assert args.user is True
        
        args = parser.parse_args(["setup-systemd", "--no-timers"])
        assert args.no_timers is True
        
        args = parser.parse_args(["setup-systemd", "-u", "--no-timers"])
        assert args.user is True
        assert args.no_timers is True
    
    def test_parser_status_command(self):
        """Test status command"""
        parser = create_argument_parser()
        
        args = parser.parse_args(["status"])
        assert args.command == "status"
    
    def test_parser_debug_command(self):
        """Test debug command"""
        parser = create_argument_parser()
        
        args = parser.parse_args(["debug"])
        assert args.command == "debug"
    
    def test_parser_storage_command(self):
        """Test storage command arguments"""
        parser = create_argument_parser()
        
        args = parser.parse_args(["storage", "--info"])
        assert args.command == "storage"
        assert args.info is True
        
        args = parser.parse_args(["storage", "--cleanup"])
        assert args.command == "storage"
        assert args.cleanup is True


class TestCommandHandlers:
    """Test individual command handlers"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_config_manager = Mock(spec=ConfigManager)
        self.mock_config = MirrorConfig()
        self.mock_config.distributions = {
            "debian": DistributionConfig(
                name="debian",
                type="apt",
                versions=["bookworm"],
                mirror_urls=["http://deb.debian.org/debian/"],
                components=["main"],
                architectures=["amd64"],
                enabled=True
            )
        }
        self.mock_config_manager.get_config.return_value = self.mock_config
        self.mock_config_manager.get_enabled_distributions.return_value = self.mock_config.distributions
    
    @pytest.mark.asyncio
    async def test_cmd_sync_all(self):
        """Test sync command with --all flag"""
        args = Mock()
        args.all = True
        args.distribution = None
        
        mock_sync_manager = Mock()
        mock_sync_manager.sync_multiple_distributions = AsyncMock(return_value=[
            {'distribution': 'debian', 'version': 'bookworm', 'status': 'completed'}
        ])
        
        with patch('builtins.print') as mock_print:
            result = await cmd_sync(args, self.mock_config_manager, mock_sync_manager)
        
        assert result == 0
        mock_sync_manager.sync_multiple_distributions.assert_called_once()
        
        # Check that success message was printed
        print_calls = [str(call) for call in mock_print.call_args_list]
        assert any("✓" in call for call in print_calls)
    
    @pytest.mark.asyncio
    async def test_cmd_sync_all_with_failures(self):
        """Test sync command with some failures"""
        args = Mock()
        args.all = True
        args.distribution = None
        
        mock_sync_manager = Mock()
        mock_sync_manager.sync_multiple_distributions = AsyncMock(return_value=[
            {'distribution': 'debian', 'version': 'bookworm', 'status': 'completed'},
            {'distribution': 'ubuntu', 'version': 'focal', 'status': 'failed', 'error': 'Network error'}
        ])
        
        with patch('builtins.print') as mock_print:
            result = await cmd_sync(args, self.mock_config_manager, mock_sync_manager)
        
        assert result == 0  # Command succeeds even with some failures
        
        print_calls = [str(call) for call in mock_print.call_args_list]
        assert any("✓" in call for call in print_calls)  # Success message
        assert any("✗" in call for call in print_calls)  # Failure message
    
    @pytest.mark.asyncio
    async def test_cmd_sync_specific_distribution(self):
        """Test sync command for specific distribution"""
        args = Mock()
        args.all = False
        args.distribution = "debian"
        args.version = "bookworm"
        
        mock_sync_manager = Mock()
        mock_sync_manager.sync_distribution = AsyncMock(return_value=[
            {'distribution': 'debian', 'version': 'bookworm', 'status': 'completed'}
        ])
        
        with patch('builtins.print') as mock_print:
            result = await cmd_sync(args, self.mock_config_manager, mock_sync_manager)
        
        assert result == 0
        mock_sync_manager.sync_distribution.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_cmd_sync_unknown_distribution(self):
        """Test sync command with unknown distribution"""
        args = Mock()
        args.all = False
        args.distribution = "nonexistent"
        args.version = None
        
        mock_sync_manager = Mock()
        
        with patch('builtins.print') as mock_print:
            result = await cmd_sync(args, self.mock_config_manager, mock_sync_manager)
        
        assert result == 1  # Error return code
        
        print_calls = [str(call) for call in mock_print.call_args_list]
        assert any("Unknown distribution" in call for call in print_calls)
    
    @pytest.mark.asyncio
    async def test_cmd_sync_no_arguments(self):
        """Test sync command without required arguments"""
        args = Mock()
        args.all = False
        args.distribution = None
        
        mock_sync_manager = Mock()
        
        with patch('builtins.print') as mock_print:
            result = await cmd_sync(args, self.mock_config_manager, mock_sync_manager)
        
        assert result == 1
        
        print_calls = [str(call) for call in mock_print.call_args_list]
        assert any("Must specify --all or --distribution" in call for call in print_calls)
    
    def test_cmd_setup_systemd_success(self):
        """Test successful systemd setup"""
        args = Mock()
        args.user = False
        args.no_timers = False
        
        mock_service_gen = Mock()
        mock_service_gen.create_all_services.return_value = [
            {
                'service_name': 'linux-mirror-debian-bookworm',
                'distribution': 'debian',
                'version': 'bookworm',
                'timer_file': '/etc/systemd/system/linux-mirror-debian-bookworm.timer'
            }
        ]
        
        with patch('src.main.SystemdServiceGenerator', return_value=mock_service_gen), \
             patch('builtins.print') as mock_print:
            
            result = cmd_setup_systemd(args, self.mock_config_manager)
        
        assert result == 0
        mock_service_gen.create_all_services.assert_called_once_with(
            user_mode=False,
            enable_timers=True
        )
        
        print_calls = [str(call) for call in mock_print.call_args_list]
        assert any("Created 1 service units" in call for call in print_calls)
    
    def test_cmd_setup_systemd_user_mode(self):
        """Test systemd setup in user mode"""
        args = Mock()
        args.user = True
        args.no_timers = True
        
        mock_service_gen = Mock()
        mock_service_gen.create_all_services.return_value = []
        
        with patch('src.main.SystemdServiceGenerator', return_value=mock_service_gen), \
             patch('builtins.print') as mock_print:
            
            result = cmd_setup_systemd(args, self.mock_config_manager)
        
        assert result == 0
        mock_service_gen.create_all_services.assert_called_once_with(
            user_mode=True,
            enable_timers=False
        )
    
    def test_cmd_setup_systemd_failure(self):
        """Test systemd setup failure"""
        args = Mock()
        args.user = False
        args.no_timers = False
        
        mock_service_gen = Mock()
        mock_service_gen.create_all_services.side_effect = Exception("Setup failed")
        
        with patch('src.main.SystemdServiceGenerator', return_value=mock_service_gen), \
             patch('builtins.print') as mock_print:
            
            result = cmd_setup_systemd(args, self.mock_config_manager)
        
        assert result == 1
        
        print_calls = [str(call) for call in mock_print.call_args_list]
        assert any("Error creating systemd services" in call for call in print_calls)
    
    def test_cmd_status(self):
        """Test status command"""
        mock_orchestrator = Mock()
        mock_orchestrator.list_running_containers.return_value = [
            {
                'name': 'linux-mirror-debian-bookworm',
                'status': 'running',
                'image': 'localhost/linux-mirror-debian:latest'
            }
        ]
        
        mock_storage_manager = Mock()
        mock_storage_manager.get_storage_info.return_value = {
            'total_repos': 5,
            'paths': [
                {
                    'path': '/srv/mirror',
                    'used_percent': 45.5,
                    'free_space': 100 * 1024**3
                }
            ]
        }
        
        with patch('builtins.print') as mock_print:
            result = cmd_status(mock_orchestrator, mock_storage_manager)
        
        assert result == 0
        
        print_calls = [str(call) for call in mock_print.call_args_list]
        assert any("Containers: 1 running" in call for call in print_calls)
        assert any("Storage: 5 repositories" in call for call in print_calls)
    
    def test_cmd_storage_info(self):
        """Test storage info command"""
        args = Mock()
        args.info = True
        args.cleanup = False
        
        mock_storage_manager = Mock()
        mock_storage_manager.get_storage_info.return_value = {
            'total_repos': 3,
            'paths': [
                {
                    'path': '/srv/mirror',
                    'type': 'base',
                    'total_size': 1000 * 1024**3,
                    'used_percent': 40.0,
                    'free_space': 600 * 1024**3,
                    'repo_count': 3
                }
            ]
        }
        
        with patch('builtins.print') as mock_print:
            result = cmd_storage(args, mock_storage_manager)
        
        assert result == 0
        
        print_calls = [str(call) for call in mock_print.call_args_list]
        assert any("Total repositories: 3" in call for call in print_calls)
    
    def test_cmd_storage_cleanup(self):
        """Test storage cleanup command"""
        args = Mock()
        args.info = False
        args.cleanup = True
        
        mock_storage_manager = Mock()
        mock_storage_manager.cleanup_old_syncs.return_value = {
            'deleted_files': 10,
            'deleted_directories': 2,
            'freed_space': 100 * 1024**2,
            'errors': []
        }
        
        with patch('builtins.print') as mock_print:
            result = cmd_storage(args, mock_storage_manager)
        
        assert result == 0
        
        print_calls = [str(call) for call in mock_print.call_args_list]
        assert any("Files deleted: 10" in call for call in print_calls)
        assert any("100.0 MB" in call for call in print_calls)
    
    def test_cmd_storage_no_arguments(self):
        """Test storage command without arguments"""
        args = Mock()
        args.info = False
        args.cleanup = False
        
        mock_storage_manager = Mock()
        
        with patch('builtins.print') as mock_print:
            result = cmd_storage(args, mock_storage_manager)
        
        assert result == 1
        
        print_calls = [str(call) for call in mock_print.call_args_list]
        assert any("Must specify --info or --cleanup" in call for call in print_calls)


class TestMainFunction:
    """Test main function and application flow"""
    
    @pytest.mark.asyncio
    @patch('src.main.setup_logging')
    @patch('src.main.ConfigManager')
    @patch('src.main.ContainerOrchestrator')
    @patch('src.main.SyncManager')
    @patch('src.main.StorageManager')
    async def test_main_no_command_interactive(self, mock_storage_manager, mock_sync_manager, 
                                             mock_orchestrator, mock_config_manager, mock_setup_logging):
        """Test main function with no command (interactive mode)"""
        mock_app = Mock()
        mock_app.run_async = AsyncMock()
        
        with patch('sys.argv', ['linux-mirrors']), \
             patch('src.main.MainInterface', return_value=mock_app):
            
            result = await main()
        
        assert result == 0
        mock_app.run_async.assert_called_once()
    
    @pytest.mark.asyncio
    @patch('src.main.setup_logging')
    @patch('src.main.ConfigManager')
    @patch('src.main.ContainerOrchestrator') 
    @patch('src.main.SyncManager')
    @patch('src.main.StorageManager')
    async def test_main_no_command_non_interactive(self, mock_storage_manager, mock_sync_manager,
                                                 mock_orchestrator, mock_config_manager, mock_setup_logging):
        """Test main function with no command in non-interactive mode"""
        with patch('sys.argv', ['linux-mirrors', '--non-interactive']):
            result = await main()
        
        assert result == 1  # Should print help and exit
    
    @pytest.mark.asyncio
    @patch('src.main.setup_logging')
    @patch('src.main.ConfigManager')
    @patch('src.main.ContainerOrchestrator')
    @patch('src.main.SyncManager')
    @patch('src.main.StorageManager')
    @patch('src.main.cmd_sync', new_callable=AsyncMock)
    async def test_main_sync_command(self, mock_cmd_sync, mock_storage_manager, mock_sync_manager,
                                   mock_orchestrator, mock_config_manager, mock_setup_logging):
        """Test main function with sync command"""
        mock_cmd_sync.return_value = 0
        
        with patch('sys.argv', ['linux-mirrors', 'sync', '--all']):
            result = await main()
        
        assert result == 0
        mock_cmd_sync.assert_called_once()
    
    @pytest.mark.asyncio
    @patch('src.main.setup_logging')
    @patch('src.main.ConfigManager')
    @patch('src.main.ContainerOrchestrator')
    @patch('src.main.SyncManager')
    @patch('src.main.StorageManager')
    @patch('src.main.cmd_setup_systemd')
    async def test_main_setup_systemd_command(self, mock_cmd_setup_systemd, mock_storage_manager,
                                            mock_sync_manager, mock_orchestrator, mock_config_manager,
                                            mock_setup_logging):
        """Test main function with setup-systemd command"""
        mock_cmd_setup_systemd.return_value = 0
        
        with patch('sys.argv', ['linux-mirrors', 'setup-systemd', '--user']):
            result = await main()
        
        assert result == 0
        mock_cmd_setup_systemd.assert_called_once()
    
    @pytest.mark.asyncio
    @patch('src.main.setup_logging')
    async def test_main_keyboard_interrupt(self, mock_setup_logging):
        """Test main function handling keyboard interrupt"""
        with patch('src.main.ConfigManager', side_effect=KeyboardInterrupt):
            result = await main()
        
        assert result == 130  # Standard exit code for SIGINT
    
    @pytest.mark.asyncio
    @patch('src.main.setup_logging')
    async def test_main_unexpected_exception(self, mock_setup_logging):
        """Test main function handling unexpected exceptions"""
        with patch('src.main.ConfigManager', side_effect=RuntimeError("Unexpected error")):
            result = await main()
        
        assert result == 1  # Error exit code
    
    @pytest.mark.asyncio
    @patch('src.main.setup_logging')
    @patch('src.main.ConfigManager')
    @patch('src.main.ContainerOrchestrator')
    @patch('src.main.SyncManager')
    @patch('src.main.StorageManager')
    async def test_main_debug_command_non_interactive(self, mock_storage_manager, mock_sync_manager,
                                                    mock_orchestrator, mock_config_manager, mock_setup_logging):
        """Test debug command in non-interactive mode fails"""
        with patch('sys.argv', ['linux-mirrors', 'debug', '--non-interactive']):
            result = await main()
        
        assert result == 1
    
    @pytest.mark.asyncio
    @patch('src.main.setup_logging')
    @patch('src.main.ConfigManager')
    @patch('src.main.ContainerOrchestrator')
    @patch('src.main.SyncManager')
    @patch('src.main.StorageManager')
    async def test_main_debug_command_interactive(self, mock_storage_manager, mock_sync_manager,
                                                mock_orchestrator, mock_config_manager, mock_setup_logging):
        """Test debug command in interactive mode"""
        mock_debug_app = Mock()
        mock_debug_app.run_async = AsyncMock()
        
        with patch('sys.argv', ['linux-mirrors', 'debug']), \
             patch('src.main.DebugInterface', return_value=mock_debug_app):
            
            result = await main()
        
        assert result == 0
        mock_debug_app.run_async.assert_called_once()
    
    @pytest.mark.asyncio
    @patch('src.main.setup_logging')
    @patch('src.main.ConfigManager')
    @patch('src.main.ContainerOrchestrator')
    @patch('src.main.SyncManager')
    @patch('src.main.StorageManager')
    async def test_main_custom_config_path(self, mock_storage_manager, mock_sync_manager,
                                         mock_orchestrator, mock_config_manager_class, mock_setup_logging):
        """Test main function with custom config path"""
        custom_path = "/custom/config.yaml"
        
        with patch('sys.argv', ['linux-mirrors', '--config', custom_path, 'status']), \
             patch('src.main.cmd_status', return_value=0):
            
            result = await main()
        
        assert result == 0
        mock_config_manager_class.assert_called_once_with(custom_path)
    
    @pytest.mark.asyncio
    @patch('src.main.setup_logging')
    @patch('src.main.ConfigManager')
    @patch('src.main.ContainerOrchestrator')
    @patch('src.main.SyncManager')
    @patch('src.main.StorageManager')
    async def test_main_custom_log_level(self, mock_storage_manager, mock_sync_manager,
                                       mock_orchestrator, mock_config_manager, mock_setup_logging):
        """Test main function with custom log level"""
        with patch('sys.argv', ['linux-mirrors', '--log-level', 'DEBUG', 'status']), \
             patch('src.main.cmd_status', return_value=0):
            
            result = await main()
        
        assert result == 0
        mock_setup_logging.assert_called_once_with('DEBUG')


@pytest.mark.integration
class TestMainIntegration:
    """Integration tests for main function"""
    
    def test_argument_parser_integration(self):
        """Test that argument parser works with real command line"""
        parser = create_argument_parser()
        
        # Test various command combinations
        test_commands = [
            ["sync", "--all"],
            ["sync", "-d", "debian", "-v", "bookworm"],
            ["setup-systemd", "--user", "--no-timers"],
            ["status"],
            ["debug"],
            ["storage", "--info"],
            ["storage", "--cleanup"]
        ]
        
        for cmd_args in test_commands:
            args = parser.parse_args(cmd_args)
            assert args.command is not None


@pytest.mark.slow
class TestMainPerformance:
    """Performance tests for main function"""
    
    @pytest.mark.asyncio
    async def test_startup_time(self):
        """Test application startup performance"""
        # This would measure startup time
        pytest.skip("Performance tests require benchmarking setup")