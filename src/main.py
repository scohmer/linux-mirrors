#!/usr/bin/env python3

import sys
import os
import argparse
import asyncio
import logging
from typing import List, Optional

from config.manager import ConfigManager
from containers.orchestrator import ContainerOrchestrator  
from sync.engines import SyncManager
from systemd.service_generator import SystemdServiceGenerator
from storage.manager import StorageManager
from tui.main_interface import MainInterface
from tui.debug_interface import DebugInterface
from verification.checker import RepositoryVerifier

def setup_logging(level: str = "INFO"):
    """Configure logging for the application"""
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # Determine log file path
    if os.geteuid() == 0:
        log_file = "/var/log/linux-mirrors.log"
    else:
        log_file = os.path.expanduser("~/.local/log/linux-mirrors.log")
        # Ensure the log directory exists
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file)
        ]
    )

def create_argument_parser() -> argparse.ArgumentParser:
    """Create and configure argument parser"""
    parser = argparse.ArgumentParser(
        description="Linux Repository Mirror Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                    # Launch TUI interface
  %(prog)s sync --all                         # Sync all enabled distributions
  %(prog)s sync --distribution debian --version bookworm
  %(prog)s setup-systemd --user              # Create systemd services for user
  %(prog)s status                             # Show container and sync status
  %(prog)s status --verify                    # Show status with repository verification
  %(prog)s status --file-integrity            # Verify GPG signatures and SHA256 checksums
  %(prog)s debug                              # Launch debug interface
        """
    )
    
    parser.add_argument(
        "--config", "-c",
        help="Path to configuration file",
        default=None
    )
    
    parser.add_argument(
        "--log-level", "-l",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Set logging level"
    )
    
    parser.add_argument(
        "--non-interactive", "-n",
        action="store_true",
        help="Run in non-interactive mode (no TUI)"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Sync command
    sync_parser = subparsers.add_parser("sync", help="Synchronize repositories")
    sync_parser.add_argument(
        "--all", "-a",
        action="store_true", 
        help="Sync all enabled distributions"
    )
    sync_parser.add_argument(
        "--distribution", "-d",
        help="Distribution name to sync"
    )
    sync_parser.add_argument(
        "--version", "-v",
        help="Specific version to sync"
    )
    
    # Setup systemd command
    systemd_parser = subparsers.add_parser("setup-systemd", help="Setup systemd services")
    systemd_parser.add_argument(
        "--user", "-u",
        action="store_true",
        help="Create user-level systemd services"
    )
    systemd_parser.add_argument(
        "--no-timers",
        action="store_true", 
        help="Don't create timer units"
    )
    
    # Status command
    status_parser = subparsers.add_parser("status", help="Show system status")
    status_parser.add_argument("--verify", action="store_true", 
                              help="Verify repository integrity against local filesystem")
    status_parser.add_argument("--file-integrity", action="store_true",
                              help="Verify file integrity with GPG signatures and SHA256 checksums")
    
    # Debug command  
    subparsers.add_parser("debug", help="Launch debug interface")
    
    # Storage command
    storage_parser = subparsers.add_parser("storage", help="Storage management")
    storage_parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Clean up old sync data"
    )
    storage_parser.add_argument(
        "--info",
        action="store_true", 
        help="Show storage information"
    )
    
    return parser

async def cmd_sync(args, config_manager: ConfigManager, sync_manager: SyncManager):
    """Handle sync command"""
    if args.all:
        print("Syncing all enabled distributions...")
        enabled_dists = config_manager.get_enabled_distributions()
        
        # Prepare distributions dict for the new sync method
        distributions = {}
        for dist_name, dist_config in enabled_dists.items():
            distributions[dist_name] = dist_config.versions
        
        print("Using optimized sync strategy (APT sequential, YUM parallel)...")
        results = await sync_manager.sync_multiple_distributions(distributions)
        
        # Group results by distribution for display
        by_dist = {}
        for result in results:
            dist_name = result.get('distribution', 'unknown')
            if dist_name not in by_dist:
                by_dist[dist_name] = []
            by_dist[dist_name].append(result)
        
        # Display results grouped by distribution
        for dist_name, dist_results in by_dist.items():
            print(f"\n{dist_name}:")
            for result in dist_results:
                status = result.get('status', 'unknown')
                version = result.get('version', 'unknown')
                if status == 'completed':
                    print(f"  ✓ {version} - completed")
                elif status == 'failed':
                    error = result.get('error', 'Unknown error')
                    print(f"  ✗ {version} - failed: {error}")
                else:
                    print(f"  ? {version} - {status}")
    
    elif args.distribution:
        dist_config = config_manager.get_config().distributions.get(args.distribution)
        if not dist_config:
            print(f"Error: Unknown distribution '{args.distribution}'")
            return 1
        
        versions = [args.version] if args.version else None
        print(f"Syncing {args.distribution} {versions or 'all versions'}...")
        
        results = await sync_manager.sync_distribution(dist_config, versions)
        
        for result in results:
            status = result.get('status', 'unknown')
            version = result.get('version', 'unknown')
            if status == 'completed':
                print(f"✓ {version} - completed")
            elif status == 'failed':
                error = result.get('error', 'Unknown error')
                print(f"✗ {version} - failed: {error}")
            else:
                print(f"? {version} - {status}")
    
    else:
        print("Error: Must specify --all or --distribution")
        return 1
    
    return 0

def cmd_setup_systemd(args, config_manager: ConfigManager):
    """Handle setup-systemd command"""
    service_gen = SystemdServiceGenerator(config_manager)
    
    try:
        created_services = service_gen.create_all_services(
            user_mode=args.user,
            enable_timers=not args.no_timers
        )
        
        print(f"Created {len(created_services)} service units:")
        for service in created_services:
            print(f"  - {service['service_name']} ({service['distribution']} {service['version']})")
        
        print(f"\nService files written to: {'~/.config/systemd/user' if args.user else '/etc/systemd/system'}")
        
        if not args.user:
            print("\nTo enable and start services, run:")
            print("  sudo systemctl daemon-reload")
            for service in created_services:
                if service.get('timer_file'):
                    print(f"  sudo systemctl enable --now {service['service_name']}.timer")
        else:
            print("\nTo enable and start services, run:")
            print("  systemctl --user daemon-reload")
            for service in created_services:
                if service.get('timer_file'):
                    print(f"  systemctl --user enable --now {service['service_name']}.timer")
        
        return 0
        
    except Exception as e:
        print(f"Error creating systemd services: {e}")
        return 1

def cmd_status(args, orchestrator: ContainerOrchestrator, storage_manager: StorageManager, config_manager: ConfigManager):
    """Handle status command"""
    if args.file_integrity:
        # File integrity verification mode (GPG + SHA256)
        print("=== Repository File Integrity Verification ===")
        verifier = RepositoryVerifier(config_manager)
        print("Checking GPG signatures and SHA256 checksums... (this may take several minutes)")
        
        verification_results = verifier.verify_all_repositories_integrity(check_signatures=True)
        
        # Summary
        total = verification_results['total_repos']
        verified = verification_results['verified']
        failed = verification_results['failed']
        missing = verification_results['missing']
        gpg_verified = verification_results['gpg_verified']
        checksums_verified = verification_results['total_checksums_verified']
        files_checked = verification_results['total_files_checked']
        packages_verified = verification_results['total_packages_verified']
        packages_checked = verification_results['total_packages_checked']
        
        print(f"\nFile integrity verification: {total} total repositories")
        print(f"  ✓ {verified} verified, ✗ {failed} failed, ? {missing} missing")
        print(f"  GPG signatures verified: {gpg_verified}/{total} repositories")
        print(f"  SHA256 checksums verified: {checksums_verified}/{files_checked} files")
        print(f"  Package integrity verified: {packages_verified}/{packages_checked} packages")
        
        # Show verified repositories
        verified_repos = [d for d in verification_results['details'] if d['status'] == 'verified']
        if verified_repos:
            print("\nVerified repositories:")
            for detail in verified_repos:
                gpg_status = "✓ GPG" if detail.get('gpg_verified', False) else "✗ GPG"
                checksum_info = f"{detail.get('checksums_verified', 0)}/{detail.get('total_files_checked', 0)} checksums"
                package_info = f"{detail.get('packages_verified', 0)}/{detail.get('total_packages', 0)} packages"
                print(f"  ✓ {detail['distribution']} {detail['version']}: {gpg_status}, {checksum_info}, {package_info}")
        
        # Show issues
        issues_found = False
        for detail in verification_results['details']:
            if detail['status'] in ['failed', 'missing']:
                if not issues_found:
                    print("\nIssues found:")
                    issues_found = True
                status_symbol = "✗" if detail['status'] == 'failed' else "?"
                gpg_status = "✓ GPG" if detail.get('gpg_verified', False) else "✗ GPG"
                checksum_info = f"{detail.get('checksums_verified', 0)}/{detail.get('total_files_checked', 0)} checksums"
                package_info = f"{detail.get('packages_verified', 0)}/{detail.get('total_packages', 0)} packages"
                print(f"  {status_symbol} {detail['distribution']} {detail['version']}: {detail['details']} ({gpg_status}, {checksum_info}, {package_info})")
        
        if not issues_found and not verified_repos:
            print("\nNo repositories found or all repositories have issues")
        
        return 0
    
    elif args.verify:
        # Repository verification only mode
        print("=== Repository Verification ===")
        verifier = RepositoryVerifier(config_manager)
        print("Checking repository integrity... (this may take a moment)")
        
        verification_results = verifier.verify_all_repositories()
        summary = verifier.get_verification_summary(verification_results)
        print(f"\n{summary}")
        
        # Show verified repositories first
        verified_repos = [d for d in verification_results['details'] if d['status'] == 'verified']
        if verified_repos:
            print("\nVerified repositories:")
            for detail in verified_repos:
                print(f"  ✓ {detail['distribution']} {detail['version']}: File integrity verified against origin repository")
        
        # Show details for failed or missing repositories
        issues_found = False
        for detail in verification_results['details']:
            if detail['status'] in ['failed', 'missing']:
                if not issues_found:
                    print("\nIssues found:")
                    issues_found = True
                status_symbol = "✗" if detail['status'] == 'failed' else "?"
                print(f"  {status_symbol} {detail['distribution']} {detail['version']}: {detail['details']}")
        
        if not issues_found and not verified_repos:
            print("\nNo repositories found or all repositories have issues")
        
        # Show verification statistics
        if verification_results['total_repos'] > 0:
            print(f"\nVerification stats:")
            print(f"  Files checked: {sum(d['files_checked'] for d in verification_results['details'])}")
            print(f"  Files missing: {sum(d['files_missing'] for d in verification_results['details'])}")
            print(f"  Files corrupted: {sum(d['files_corrupted'] for d in verification_results['details'])}")
    
    else:
        # Standard system status mode
        print("=== Linux Mirror System Status ===\n")
        
        # Container status
        containers = orchestrator.list_running_containers()
        print(f"Containers: {len(containers)} running")
        for container in containers:
            print(f"  {container['name']}: {container['status']} ({container['image']})")
        
        print()
        
        # Storage status
        storage_info = storage_manager.get_storage_info()
        print(f"Storage: {storage_info['total_repos']} repositories")
        for path_info in storage_info['paths']:
            path = path_info['path']
            used_pct = path_info.get('used_percent', 0)
            free_gb = path_info.get('free_space', 0) / (1024**3)
            print(f"  {path}: {used_pct:.1f}% used, {free_gb:.1f}GB free")
    
    return 0

def cmd_storage(args, storage_manager: StorageManager):
    """Handle storage command"""
    if args.info:
        storage_info = storage_manager.get_storage_info()
        print("=== Storage Information ===")
        print(f"Total repositories: {storage_info['total_repos']}")
        
        for path_info in storage_info['paths']:
            print(f"\nPath: {path_info['path']}")
            print(f"  Type: {path_info.get('type', 'unknown')}")
            print(f"  Total size: {path_info.get('total_size', 0) / (1024**3):.1f} GB")
            print(f"  Used: {path_info.get('used_percent', 0):.1f}%")
            print(f"  Free space: {path_info.get('free_space', 0) / (1024**3):.1f} GB")
            print(f"  Repositories: {path_info.get('repo_count', 0)}")
    
    elif args.cleanup:
        print("Cleaning up old sync data...")
        result = storage_manager.cleanup_old_syncs()
        
        print(f"Cleanup completed:")
        print(f"  Files deleted: {result['deleted_files']}")
        print(f"  Directories deleted: {result['deleted_directories']}")
        print(f"  Space freed: {result['freed_space'] / (1024**2):.1f} MB")
        
        if result['errors']:
            print(f"  Errors: {len(result['errors'])}")
            for error in result['errors']:
                print(f"    - {error}")
    
    else:
        print("Error: Must specify --info or --cleanup")
        return 1
    
    return 0

async def main():
    """Main entry point"""
    parser = create_argument_parser()
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)
    
    try:
        # Initialize components
        config_manager = ConfigManager(args.config)
        orchestrator = ContainerOrchestrator(config_manager)
        sync_manager = SyncManager(orchestrator)
        storage_manager = StorageManager(config_manager)
        
        # Ensure directory structure exists
        storage_manager.ensure_directory_structure()
        
        # Route to appropriate command handler
        if args.command == "sync":
            return await cmd_sync(args, config_manager, sync_manager)
        
        elif args.command == "setup-systemd":
            return cmd_setup_systemd(args, config_manager)
        
        elif args.command == "status":
            return cmd_status(args, orchestrator, storage_manager, config_manager)
        
        elif args.command == "storage":
            return cmd_storage(args, storage_manager)
        
        elif args.command == "debug":
            if args.non_interactive:
                print("Debug command requires interactive mode")
                return 1
            
            from textual.app import App
            
            class DebugApp(App):
                def on_mount(self):
                    debug_screen = DebugInterface()
                    # Mark this as standalone mode
                    debug_screen.standalone_mode = True
                    self.push_screen(debug_screen)
            
            debug_app = DebugApp()
            await debug_app.run_async()
            return 0
        
        else:
            # No command specified - launch TUI
            if args.non_interactive:
                parser.print_help()
                return 1
            
            app = MainInterface()
            await app.run_async()
            return 0
    
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130
    
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        if args.log_level == "DEBUG":
            import traceback
            traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)