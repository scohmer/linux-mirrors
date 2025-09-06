#!/usr/bin/env python3

import os
import shutil
import logging
import psutil
from typing import Dict, List, Optional, Any
from pathlib import Path
from datetime import datetime, timedelta
from ..config.manager import ConfigManager

logger = logging.getLogger(__name__)

class StorageManager:
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.config = config_manager.get_config()
    
    def ensure_directory_structure(self) -> Dict[str, bool]:
        """Create all required mirror directories"""
        directories = [
            self.config.base_path,
            self.config.apt_path,
            self.config.yum_path
        ]
        
        # Add distribution-specific directories
        for dist_name, dist_config in self.config.distributions.items():
            if dist_config.enabled:
                dist_path = self.config_manager.get_distribution_path(dist_name)
                directories.append(dist_path)
                
                # Add version subdirectories
                for version in dist_config.versions:
                    version_path = os.path.join(dist_path, version)
                    directories.append(version_path)
        
        results = {}
        for directory in directories:
            try:
                os.makedirs(directory, mode=0o755, exist_ok=True)
                results[directory] = True
                logger.debug(f"Ensured directory exists: {directory}")
            except Exception as e:
                logger.error(f"Failed to create directory {directory}: {e}")
                results[directory] = False
        
        return results
    
    def get_storage_info(self) -> Dict[str, Any]:
        """Get comprehensive storage information"""
        storage_info = {
            'base_path': self.config.base_path,
            'paths': [],
            'total_repos': 0,
            'last_updated': datetime.now().isoformat()
        }
        
        # Check each main path
        for path_type, path in [
            ('base', self.config.base_path),
            ('apt', self.config.apt_path),
            ('yum', self.config.yum_path)
        ]:
            path_info = self._get_path_info(path, path_type)
            if path_info:
                storage_info['paths'].append(path_info)
        
        # Count total repositories
        storage_info['total_repos'] = self._count_repositories()
        
        return storage_info
    
    def _get_path_info(self, path: str, path_type: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific path"""
        if not os.path.exists(path):
            return None
        
        try:
            # Get disk usage for the mount point
            disk_usage = psutil.disk_usage(path)
            
            # Get directory size
            dir_size = self._get_directory_size(path)
            
            # Get number of subdirectories (repositories)
            repo_count = len([d for d in os.listdir(path) 
                            if os.path.isdir(os.path.join(path, d))])
            
            return {
                'path': path,
                'type': path_type,
                'total_size': disk_usage.total,
                'used_space': disk_usage.used,
                'free_space': disk_usage.free,
                'used_percent': (disk_usage.used / disk_usage.total) * 100,
                'directory_size': dir_size,
                'repo_count': repo_count,
                'last_accessed': os.path.getatime(path),
                'last_modified': os.path.getmtime(path)
            }
            
        except Exception as e:
            logger.error(f"Failed to get info for path {path}: {e}")
            return None
    
    def _get_directory_size(self, path: str) -> int:
        """Calculate total size of a directory recursively"""
        total_size = 0
        try:
            for dirpath, dirnames, filenames in os.walk(path):
                for filename in filenames:
                    file_path = os.path.join(dirpath, filename)
                    try:
                        total_size += os.path.getsize(file_path)
                    except (OSError, FileNotFoundError):
                        # Skip files that can't be accessed
                        continue
        except Exception as e:
            logger.warning(f"Error calculating directory size for {path}: {e}")
        
        return total_size
    
    def _count_repositories(self) -> int:
        """Count total number of repositories across all distributions"""
        total_repos = 0
        
        for dist_name, dist_config in self.config.distributions.items():
            if not dist_config.enabled:
                continue
                
            dist_path = self.config_manager.get_distribution_path(dist_name)
            if os.path.exists(dist_path):
                # Count version directories
                total_repos += len([d for d in os.listdir(dist_path) 
                                  if os.path.isdir(os.path.join(dist_path, d))])
        
        return total_repos
    
    def cleanup_old_syncs(self, days_old: int = 30) -> Dict[str, Any]:
        """Clean up old sync data and temporary files"""
        cleanup_result = {
            'freed_space': 0,
            'deleted_files': 0,
            'deleted_directories': 0,
            'errors': []
        }
        
        cutoff_date = datetime.now() - timedelta(days=days_old)
        cutoff_timestamp = cutoff_date.timestamp()
        
        # Look for temporary and old files in each distribution directory
        for dist_name, dist_config in self.config.distributions.items():
            if not dist_config.enabled:
                continue
            
            dist_path = self.config_manager.get_distribution_path(dist_name)
            if not os.path.exists(dist_path):
                continue
            
            try:
                cleanup_result.update(
                    self._cleanup_directory(dist_path, cutoff_timestamp)
                )
            except Exception as e:
                error_msg = f"Failed to cleanup {dist_path}: {e}"
                logger.error(error_msg)
                cleanup_result['errors'].append(error_msg)
        
        return cleanup_result
    
    def _cleanup_directory(self, directory: str, cutoff_timestamp: float) -> Dict[str, int]:
        """Clean up old files and directories in a specific directory"""
        result = {
            'freed_space': 0,
            'deleted_files': 0,
            'deleted_directories': 0
        }
        
        for root, dirs, files in os.walk(directory, topdown=False):
            # Clean up old files
            for filename in files:
                file_path = os.path.join(root, filename)
                try:
                    file_stat = os.stat(file_path)
                    
                    # Check if file is old and matches cleanup patterns
                    if (file_stat.st_mtime < cutoff_timestamp and 
                        self._should_cleanup_file(filename)):
                        
                        file_size = file_stat.st_size
                        os.remove(file_path)
                        result['freed_space'] += file_size
                        result['deleted_files'] += 1
                        logger.debug(f"Deleted old file: {file_path}")
                        
                except Exception as e:
                    logger.warning(f"Failed to delete file {file_path}: {e}")
            
            # Clean up empty directories (except main distribution directories)
            for dirname in dirs:
                dir_path = os.path.join(root, dirname)
                try:
                    if (os.path.getmtime(dir_path) < cutoff_timestamp and
                        self._is_empty_directory(dir_path) and
                        not self._is_protected_directory(dir_path)):
                        
                        shutil.rmtree(dir_path)
                        result['deleted_directories'] += 1
                        logger.debug(f"Deleted empty directory: {dir_path}")
                        
                except Exception as e:
                    logger.warning(f"Failed to delete directory {dir_path}: {e}")
        
        return result
    
    def _should_cleanup_file(self, filename: str) -> bool:
        """Determine if a file should be cleaned up"""
        cleanup_patterns = [
            '.tmp', '.temp', '.partial', '.download',
            'lock', '.lock', '.pid', '.log'
        ]
        
        filename_lower = filename.lower()
        return any(pattern in filename_lower for pattern in cleanup_patterns)
    
    def _is_empty_directory(self, directory: str) -> bool:
        """Check if a directory is empty"""
        try:
            return len(os.listdir(directory)) == 0
        except Exception:
            return False
    
    def _is_protected_directory(self, directory: str) -> bool:
        """Check if a directory should be protected from cleanup"""
        protected_names = ['mirror', 'skel', 'var', 'repodata', 'dists', 'pool']
        dir_name = os.path.basename(directory).lower()
        return dir_name in protected_names
    
    def check_disk_space(self, required_gb: float = 10.0) -> Dict[str, Any]:
        """Check if there's enough disk space for syncing"""
        space_check = {
            'sufficient_space': True,
            'available_gb': 0,
            'required_gb': required_gb,
            'paths': {}
        }
        
        paths_to_check = [
            self.config.base_path,
            self.config.apt_path,
            self.config.yum_path
        ]
        
        for path in paths_to_check:
            try:
                if os.path.exists(path):
                    disk_usage = psutil.disk_usage(path)
                    available_gb = disk_usage.free / (1024**3)  # Convert to GB
                    
                    space_check['paths'][path] = {
                        'available_gb': available_gb,
                        'sufficient': available_gb >= required_gb
                    }
                    
                    if available_gb < required_gb:
                        space_check['sufficient_space'] = False
                    
                    # Update overall available space (minimum across paths)
                    if space_check['available_gb'] == 0 or available_gb < space_check['available_gb']:
                        space_check['available_gb'] = available_gb
                        
            except Exception as e:
                logger.error(f"Failed to check disk space for {path}: {e}")
                space_check['paths'][path] = {'error': str(e)}
        
        return space_check
    
    def create_backup(self, dist_name: str, version: str) -> Optional[str]:
        """Create a backup of a specific distribution version"""
        try:
            source_path = os.path.join(
                self.config_manager.get_distribution_path(dist_name),
                version
            )
            
            if not os.path.exists(source_path):
                logger.error(f"Source path does not exist: {source_path}")
                return None
            
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_name = f"{dist_name}-{version}-backup-{timestamp}"
            backup_path = os.path.join(
                self.config.base_path,
                "backups",
                backup_name
            )
            
            # Ensure backup directory exists
            os.makedirs(os.path.dirname(backup_path), exist_ok=True)
            
            # Create backup
            shutil.copytree(source_path, backup_path)
            logger.info(f"Created backup: {backup_path}")
            
            return backup_path
            
        except Exception as e:
            logger.error(f"Failed to create backup for {dist_name} {version}: {e}")
            return None
    
    def restore_backup(self, backup_path: str, dist_name: str, version: str) -> bool:
        """Restore from a backup"""
        try:
            if not os.path.exists(backup_path):
                logger.error(f"Backup path does not exist: {backup_path}")
                return False
            
            target_path = os.path.join(
                self.config_manager.get_distribution_path(dist_name),
                version
            )
            
            # Remove existing directory if it exists
            if os.path.exists(target_path):
                shutil.rmtree(target_path)
            
            # Restore from backup
            shutil.copytree(backup_path, target_path)
            logger.info(f"Restored backup from {backup_path} to {target_path}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to restore backup: {e}")
            return False