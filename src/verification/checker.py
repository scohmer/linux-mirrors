#!/usr/bin/env python3

import os
import logging
import hashlib
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from urllib.parse import urljoin
import requests
import gzip
import tempfile

from config.manager import ConfigManager, DistributionConfig

logger = logging.getLogger(__name__)

class RepositoryVerifier:
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.config = config_manager.get_config()
    
    def verify_all_repositories(self) -> Dict[str, Any]:
        """Verify all enabled repositories and return summary"""
        results = {
            'total_repos': 0,
            'verified': 0,
            'failed': 0,
            'missing': 0,
            'details': []
        }
        
        for dist_name, dist_config in self.config.distributions.items():
            if not dist_config.enabled:
                continue
                
            for version in dist_config.versions:
                results['total_repos'] += 1
                verification_result = self.verify_repository(dist_name, version, dist_config)
                results['details'].append(verification_result)
                
                if verification_result['status'] == 'verified':
                    results['verified'] += 1
                elif verification_result['status'] == 'failed':
                    results['failed'] += 1
                elif verification_result['status'] == 'missing':
                    results['missing'] += 1
        
        return results
    
    def verify_repository(self, dist_name: str, version: str, dist_config: DistributionConfig) -> Dict[str, Any]:
        """Verify a specific repository against its upstream source"""
        repo_path = self._get_repository_path(dist_name, version, dist_config)
        
        if not os.path.exists(repo_path):
            return {
                'distribution': dist_name,
                'version': version,
                'status': 'missing',
                'path': repo_path,
                'details': f'Repository directory not found at {repo_path}',
                'files_checked': 0,
                'files_missing': 0,
                'files_corrupted': 0
            }
        
        try:
            if dist_config.type == 'apt':
                return self._verify_apt_repository(dist_name, version, dist_config, repo_path)
            elif dist_config.type == 'yum':
                return self._verify_yum_repository(dist_name, version, dist_config, repo_path)
            else:
                return {
                    'distribution': dist_name,
                    'version': version,
                    'status': 'failed',
                    'path': repo_path,
                    'details': f'Unknown repository type: {dist_config.type}',
                    'files_checked': 0,
                    'files_missing': 0,
                    'files_corrupted': 0
                }
        except Exception as e:
            logger.error(f"Error verifying {dist_name} {version}: {e}")
            return {
                'distribution': dist_name,
                'version': version,
                'status': 'failed',
                'path': repo_path,
                'details': f'Verification error: {e}',
                'files_checked': 0,
                'files_missing': 0,
                'files_corrupted': 0
            }
    
    def _get_available_architectures(self, dist_name: str, version: str, configured_archs: List[str]) -> List[str]:
        """Get architectures that were actually available for a specific distribution version"""
        if dist_name == 'debian':
            # Debian architecture availability by version
            if version == 'wheezy':  # Debian 7 (2013)
                # wheezy had limited architecture support, 'all' arch may not be reliable
                available_archs = ['amd64', 'i386', 'armhf']
            elif version == 'jessie':  # Debian 8 (2015) 
                # jessie introduced arm64 but still limited, 'all' arch should be available
                available_archs = ['amd64', 'i386', 'armhf', 'arm64', 'all']
            elif version in ['stretch', 'buster']:  # Debian 9-10 (2017-2019)
                # Full architecture support including 'all'
                available_archs = ['amd64', 'i386', 'armhf', 'arm64', 'all']
            else:  # bullseye, bookworm, etc. (Debian 11+)
                # All architectures available including 'all'
                available_archs = ['amd64', 'i386', 'armhf', 'arm64', 'all']
            
            # Filter configured architectures by what's actually available
            return [arch for arch in configured_archs if arch in available_archs]
        
        elif dist_name == 'ubuntu':
            # Ubuntu architecture availability including 'all' architecture
            if version in ['bionic', 'focal']:  # 18.04, 20.04
                available_archs = ['amd64', 'i386', 'armhf', 'arm64', 'all']  
            else:  # jammy, noble, oracular and newer
                available_archs = ['amd64', 'i386', 'armhf', 'arm64', 'all']
            
            return [arch for arch in configured_archs if arch in available_archs]
        
        elif dist_name in ['rocky', 'rhel']:
            # For Rocky/RHEL, respect user's configured architectures first
            # Only filter out architectures that are definitely not supported for the version
            unsupported_archs = []
            
            if version == '8':
                # Rocky/RHEL 8 doesn't support ppc64le, s390x, riscv64
                unsupported_archs = ['ppc64le', 's390x', 'riscv64']
            elif version == '9':
                # Rocky/RHEL 9 doesn't support riscv64
                unsupported_archs = ['riscv64']
            # Rocky/RHEL 10 supports all architectures
            
            # Return configured architectures, excluding any that are definitely unsupported
            return [arch for arch in configured_archs if arch not in unsupported_archs]
        
        # For other distributions or unknown versions, use all configured architectures
        return configured_archs

    def _verify_apt_repository(self, dist_name: str, version: str, dist_config: DistributionConfig, repo_path: str) -> Dict[str, Any]:
        """Verify APT repository by checking Release file and key packages"""
        files_checked = 0
        files_missing = 0
        files_corrupted = 0
        details = []
        
        # Check for basic APT structure
        dists_path = os.path.join(repo_path, 'dists', version)
        if not os.path.exists(dists_path):
            return {
                'distribution': dist_name,
                'version': version,
                'status': 'missing',
                'path': repo_path,
                'details': f'Missing dists/{version} directory',
                'files_checked': 0,
                'files_missing': 1,
                'files_corrupted': 0
            }
        
        # Check Release file
        release_file = os.path.join(dists_path, 'Release')
        files_checked += 1
        if not os.path.exists(release_file):
            files_missing += 1
            details.append('Missing Release file')
        else:
            # Verify Release file has expected content
            try:
                with open(release_file, 'r') as f:
                    content = f.read()
                    if f"Suite: {version}" not in content and f"Codename: {version}" not in content:
                        files_corrupted += 1
                        details.append('Release file may be corrupted (missing suite/codename)')
            except Exception as e:
                files_corrupted += 1
                details.append(f'Error reading Release file: {e}')
        
        # Check for component directories and key files
        # Get architectures that were actually available for this distribution version
        available_architectures = self._get_available_architectures(dist_name, version, dist_config.architectures or ['amd64'])
        
        for component in dist_config.components or ['main']:
            for arch in available_architectures:
                # Skip 'all' architecture - these packages are included in main architecture Packages files
                if arch == 'all':
                    continue
                    
                # Check Packages file
                packages_path = os.path.join(dists_path, component, f'binary-{arch}', 'Packages.gz')
                files_checked += 1
                if not os.path.exists(packages_path):
                    packages_path = os.path.join(dists_path, component, f'binary-{arch}', 'Packages')
                    if not os.path.exists(packages_path):
                        files_missing += 1
                        details.append(f'Missing Packages file for {component}/{arch}')
                
                # Check if pool directory has some packages
                pool_path = os.path.join(repo_path, 'pool', component)
                if os.path.exists(pool_path):
                    # Count .deb files as a basic check
                    deb_count = len(list(Path(pool_path).rglob('*.deb')))
                    if deb_count == 0:
                        details.append(f'No .deb packages found in pool/{component}')
        
        # Determine overall status
        if files_missing > 0:
            status = 'missing'
        elif files_corrupted > 0:
            status = 'failed'
        elif files_checked > 0:
            status = 'verified'
        else:
            status = 'failed'
        
        return {
            'distribution': dist_name,
            'version': version,
            'status': status,
            'path': repo_path,
            'details': '; '.join(details) if details else 'Repository structure verified',
            'files_checked': files_checked,
            'files_missing': files_missing,
            'files_corrupted': files_corrupted
        }
    
    def _verify_yum_repository(self, dist_name: str, version: str, dist_config: DistributionConfig, repo_path: str) -> Dict[str, Any]:
        """Verify YUM repository by checking repodata and key packages"""
        files_checked = 0
        files_missing = 0
        files_corrupted = 0
        details = []
        
        # YUM repos have different structure per version/architecture
        # Get architectures that were actually available for this distribution version
        configured_archs = dist_config.architectures or ['x86_64']
        available_architectures = self._get_available_architectures(dist_name, version, configured_archs)
        
        logger.debug(f"YUM verification for {dist_name} {version}: configured_archs={configured_archs}, available_architectures={available_architectures}")
        
        for arch in available_architectures:
            arch_found = False
            
            # Check common YUM repository structures
            possible_paths = [
                os.path.join(repo_path, version, 'BaseOS', arch, 'os'),
                os.path.join(repo_path, version, 'AppStream', arch, 'os'),
                os.path.join(repo_path, version, arch),
                os.path.join(repo_path, arch),
            ]
            
            for check_path in possible_paths:
                if os.path.exists(check_path):
                    arch_found = True
                    repodata_path = os.path.join(check_path, 'repodata')
                    files_checked += 1
                    
                    if not os.path.exists(repodata_path):
                        files_missing += 1
                        details.append(f'Missing repodata for {arch} at {check_path}')
                        continue
                    
                    # Check for repomd.xml
                    repomd_file = os.path.join(repodata_path, 'repomd.xml')
                    files_checked += 1
                    if not os.path.exists(repomd_file):
                        files_missing += 1
                        details.append(f'Missing repomd.xml for {arch}')
                    else:
                        # Basic validation of repomd.xml
                        try:
                            with open(repomd_file, 'r') as f:
                                content = f.read()
                                if '<repomd' not in content:
                                    files_corrupted += 1
                                    details.append(f'Invalid repomd.xml for {arch}')
                        except Exception as e:
                            files_corrupted += 1
                            details.append(f'Error reading repomd.xml for {arch}: {e}')
                    
                    # Check for RPM packages
                    rpm_count = len(list(Path(check_path).rglob('*.rpm')))
                    if rpm_count == 0:
                        # Check Packages subdirectory
                        packages_dir = os.path.join(check_path, 'Packages')
                        if os.path.exists(packages_dir):
                            rpm_count = len(list(Path(packages_dir).rglob('*.rpm')))
                        
                        if rpm_count == 0:
                            details.append(f'No RPM packages found for {arch} at {check_path}')
                    
                    break
            
            if not arch_found:
                files_missing += 1
                details.append(f'No repository structure found for architecture {arch}')
        
        # Determine overall status
        if files_missing > 0:
            status = 'missing'
        elif files_corrupted > 0:
            status = 'failed'
        elif files_checked > 0:
            status = 'verified'
        else:
            status = 'failed'
        
        return {
            'distribution': dist_name,
            'version': version,
            'status': status,
            'path': repo_path,
            'details': '; '.join(details) if details else 'Repository structure verified',
            'files_checked': files_checked,
            'files_missing': files_missing,
            'files_corrupted': files_corrupted
        }
    
    def _get_repository_path(self, dist_name: str, version: str, dist_config: DistributionConfig) -> str:
        """Get the local path for a repository"""
        if dist_config.type == 'apt':
            base_path = os.path.join(self.config.apt_path, dist_name)
            
            # apt-mirror creates mirror/hostname/path structure
            # Try to find the actual repository path
            mirror_paths = []
            
            # Check for apt-mirror structure
            for mirror_url in dist_config.mirror_urls:
                # Extract hostname and path from mirror URL
                # e.g., "http://deb.debian.org/debian/" -> "mirror/deb.debian.org/debian"
                url_parts = mirror_url.replace('http://', '').replace('https://', '').rstrip('/')
                mirror_path = os.path.join(base_path, 'mirror', url_parts)
                mirror_paths.append(mirror_path)
                
                # Also check without the 'mirror' prefix (direct structure)
                direct_path = os.path.join(base_path, url_parts)
                mirror_paths.append(direct_path)
            
            # For Debian, handle archive.debian.org vs deb.debian.org paths
            if dist_name == 'debian':
                # Debian versions that use archive.debian.org (versions < 11)
                archive_versions = ['wheezy', 'jessie', 'stretch', 'buster']
                
                if version in archive_versions:
                    # For archive versions, check archive.debian.org paths first
                    archive_mirror_path = os.path.join(base_path, 'mirror', 'archive.debian.org', 'debian')
                    archive_direct_path = os.path.join(base_path, 'archive.debian.org', 'debian')
                    
                    # Insert at beginning to prioritize archive paths for older versions
                    mirror_paths.insert(0, archive_mirror_path)
                    mirror_paths.insert(1, archive_direct_path)
                    
                    logger.debug(f"Archive Debian version {version} - prioritizing archive.debian.org paths")
                
                else:
                    # For current versions (>= 11), check deb.debian.org paths first
                    deb_mirror_path = os.path.join(base_path, 'mirror', 'deb.debian.org', 'debian')
                    deb_direct_path = os.path.join(base_path, 'deb.debian.org', 'debian')
                    
                    # Only add if not already in the list from mirror_urls processing
                    if deb_mirror_path not in mirror_paths:
                        mirror_paths.insert(0, deb_mirror_path)
                    if deb_direct_path not in mirror_paths:
                        mirror_paths.insert(1, deb_direct_path)
                    
                    logger.debug(f"Current Debian version {version} - prioritizing deb.debian.org paths")
            
            # Also check the base path itself (in case it's structured differently)
            mirror_paths.append(base_path)
            
            # Return the first path that exists and has dists directory
            logger.debug(f"Checking APT repository paths for {dist_name}: {mirror_paths}")
            for path in mirror_paths:
                logger.debug(f"Checking path: {path}")
                if os.path.exists(path):
                    logger.debug(f"Path exists: {path}")
                    dists_path = os.path.join(path, 'dists')
                    if os.path.exists(dists_path):
                        logger.debug(f"Found dists directory at: {dists_path}")
                        return path
                    else:
                        logger.debug(f"No dists directory found at: {dists_path}")
                else:
                    logger.debug(f"Path does not exist: {path}")
            
            # If none found, return the first mirror path for error reporting
            logger.debug(f"No valid APT repository path found for {dist_name}, returning: {mirror_paths[0] if mirror_paths else base_path}")
            return mirror_paths[0] if mirror_paths else base_path
            
        else:  # yum
            return os.path.join(self.config.yum_path, dist_name)
    
    def get_verification_summary(self, results: Dict[str, Any]) -> str:
        """Get a human-readable summary of verification results"""
        total = results['total_repos']
        verified = results['verified']
        failed = results['failed']
        missing = results['missing']
        
        if total == 0:
            return "No repositories configured for verification"
        
        summary_parts = []
        if verified > 0:
            summary_parts.append(f"{verified} verified")
        if missing > 0:
            summary_parts.append(f"{missing} missing")
        if failed > 0:
            summary_parts.append(f"{failed} failed")
        
        return f"Repository verification: {total} total, " + ", ".join(summary_parts)