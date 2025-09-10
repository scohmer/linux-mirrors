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
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing

from config.manager import ConfigManager, DistributionConfig

logger = logging.getLogger(__name__)

class RepositoryVerifier:
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.config = config_manager.get_config()
        
        # Common GPG key IDs and URLs for distributions
        self.gpg_keys = {
            'kali': {
                'ED65462EC8D5E4C5': 'https://archive.kali.org/archive-key.asc',
                '827C8569F2518CC677FECA1AED65462EC8D5E4C5': 'https://archive.kali.org/archive-key.asc'
            },
            'debian': {
                # Current Debian signing keys
                'DC30D7C23CBBABEE': 'https://ftp-master.debian.org/keys/archive-key-11.asc',
                'E0B11894F66AEC98': 'https://ftp-master.debian.org/keys/archive-key-12.asc',
                # Additional Debian keys from the logs
                '8B48AD6246925553': 'https://ftp-master.debian.org/keys/archive-key-8.asc',
                'A1BD8E9D78F7FE5C3E65D8AF8B48AD6246925553': 'https://ftp-master.debian.org/keys/archive-key-8.asc',
                '16E90B3FDF65EDE3AA7F323C04EE7237B7D453EC': 'https://ftp-master.debian.org/keys/archive-key-9.asc', 
                '0146DC6D4A0B2914BDED34DB648ACFD622F3D138': 'https://ftp-master.debian.org/keys/archive-key-10.asc',
                'A7236886F3CCCAAD148A27F80E98404D386FA1D9': 'https://ftp-master.debian.org/keys/archive-key-10.asc',
                '4CB50190207B4758A3F73A796ED0E7B82643E131': 'https://ftp-master.debian.org/keys/archive-key-11.asc',
            },
            'ubuntu': {
                # Current Ubuntu signing keys
                'C0B21F32': 'https://keyserver.ubuntu.com/pks/lookup?op=get&search=0xC0B21F32',
                '790BC7277767219C42C86F933B4FE6ACC0B21F32': 'https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x790BC7277767219C42C86F933B4FE6ACC0B21F32',
                '3B4FE6ACC0B21F32': 'https://keyserver.ubuntu.com/pks/lookup?op=get&search=0xC0B21F32',
                # Additional Ubuntu keys from the logs
                '871920D1991BC93C': 'https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x871920D1991BC93C',
                'F6ECB3762474EDA9D21B7022871920D1991BC93C': 'https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x871920D1991BC93C',
            }
        }
    
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
                # jessie introduced arm64 but 'all' arch binary-all directories don't exist
                available_archs = ['amd64', 'i386', 'armhf', 'arm64']
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
            
            # For Ubuntu, handle EOL versions that use old-releases.ubuntu.com
            elif dist_name == 'ubuntu':
                # Ubuntu versions that have reached EOL and moved to old-releases
                eol_versions = ["mantic"]  # Ubuntu 23.10 - EOL July 11, 2024
                
                if version in eol_versions:
                    # For EOL versions, check old-releases.ubuntu.com paths first
                    old_releases_mirror_path = os.path.join(base_path, 'mirror', 'old-releases.ubuntu.com', 'ubuntu')
                    old_releases_direct_path = os.path.join(base_path, 'old-releases.ubuntu.com', 'ubuntu')
                    
                    # Insert at beginning to prioritize old-releases paths for EOL versions
                    mirror_paths.insert(0, old_releases_mirror_path)
                    mirror_paths.insert(1, old_releases_direct_path)
                    
                    logger.debug(f"EOL Ubuntu version {version} - prioritizing old-releases.ubuntu.com paths")
                
                else:
                    # For current versions, check archive.ubuntu.com paths first
                    archive_mirror_path = os.path.join(base_path, 'mirror', 'archive.ubuntu.com', 'ubuntu')
                    archive_direct_path = os.path.join(base_path, 'archive.ubuntu.com', 'ubuntu')
                    
                    # Only add if not already in the list from mirror_urls processing
                    if archive_mirror_path not in mirror_paths:
                        mirror_paths.insert(0, archive_mirror_path)
                    if archive_direct_path not in mirror_paths:
                        mirror_paths.insert(1, archive_direct_path)
                    
                    logger.debug(f"Current Ubuntu version {version} - prioritizing archive.ubuntu.com paths")
            
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
    
    def verify_all_repositories_integrity(self, check_signatures: bool = True, max_workers: Optional[int] = None) -> Dict[str, Any]:
        """Verify file integrity for all enabled repositories including GPG and checksums with parallel processing"""
        results = {
            'total_repos': 0,
            'verified': 0,
            'failed': 0,
            'missing': 0,
            'gpg_verified': 0,
            'total_checksums_verified': 0,
            'total_files_checked': 0,
            'total_packages_verified': 0,
            'total_packages_checked': 0,
            'details': []
        }
        
        # Collect all repository tasks
        repository_tasks = []
        for dist_name, dist_config in self.config.distributions.items():
            if not dist_config.enabled:
                continue
            for version in dist_config.versions:
                results['total_repos'] += 1
                repository_tasks.append((dist_name, version, dist_config, check_signatures))
        
        if not repository_tasks:
            return results
        
        # Determine optimal number of workers
        if max_workers is None:
            # Use CPU count for I/O bound operations like file reads and hashing
            max_workers = min(len(repository_tasks), max(2, multiprocessing.cpu_count()))
        
        logger.info(f"Starting parallel verification of {len(repository_tasks)} repositories using {max_workers} workers")
        
        # Process repositories in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_repo = {
                executor.submit(self.verify_file_integrity, dist_name, version, dist_config, check_signatures): 
                (dist_name, version) 
                for dist_name, version, dist_config, check_signatures in repository_tasks
            }
            
            # Collect results as they complete
            completed_count = 0
            for future in as_completed(future_to_repo):
                dist_name, version = future_to_repo[future]
                completed_count += 1
                
                try:
                    verification_result = future.result()
                    results['details'].append(verification_result)
                    
                    # Update status counters
                    if verification_result['status'] == 'verified':
                        results['verified'] += 1
                    elif verification_result['status'] == 'failed':
                        results['failed'] += 1
                    elif verification_result['status'] == 'missing':
                        results['missing'] += 1
                    
                    # Aggregate integrity stats
                    if verification_result.get('gpg_verified', False):
                        results['gpg_verified'] += 1
                    results['total_checksums_verified'] += verification_result.get('checksums_verified', 0)
                    results['total_files_checked'] += verification_result.get('total_files_checked', 0)
                    results['total_packages_verified'] += verification_result.get('packages_verified', 0)
                    results['total_packages_checked'] += verification_result.get('total_packages', 0)
                    
                    # Log progress
                    logger.info(f"Completed verification {completed_count}/{len(repository_tasks)}: {dist_name} {version}")
                    
                except Exception as e:
                    logger.error(f"Error verifying {dist_name} {version}: {e}")
                    # Create a failed result for the exception
                    error_result = {
                        'distribution': dist_name,
                        'version': version,
                        'status': 'failed',
                        'path': 'unknown',
                        'details': f'Verification failed with exception: {e}',
                        'gpg_verified': False,
                        'checksums_verified': 0,
                        'total_files_checked': 0,
                        'packages_verified': 0,
                        'total_packages': 0
                    }
                    results['details'].append(error_result)
                    results['failed'] += 1
        
        logger.info(f"Parallel verification completed: {results['verified']} verified, {results['failed']} failed, {results['missing']} missing")
        return results
    
    def verify_file_integrity(self, dist_name: str, version: str, dist_config: DistributionConfig, check_signatures: bool = True) -> Dict[str, Any]:
        """Verify file integrity including GPG signatures and SHA256 checksums"""
        repo_path = self._get_repository_path(dist_name, version, dist_config)
        
        if not os.path.exists(repo_path):
            return {
                'distribution': dist_name,
                'version': version,
                'status': 'missing',
                'path': repo_path,
                'details': f'Repository directory not found at {repo_path}',
                'gpg_verified': False,
                'checksums_verified': 0,
                'total_files_checked': 0
            }
        
        try:
            if dist_config.type == 'apt':
                return self._verify_apt_file_integrity(dist_name, version, dist_config, repo_path, check_signatures)
            elif dist_config.type == 'yum':
                return self._verify_yum_file_integrity(dist_name, version, dist_config, repo_path, check_signatures)
            else:
                return {
                    'distribution': dist_name,
                    'version': version,
                    'status': 'failed',
                    'path': repo_path,
                    'details': f'Unknown repository type: {dist_config.type}',
                    'gpg_verified': False,
                    'checksums_verified': 0,
                    'total_files_checked': 0
                }
        except Exception as e:
            logger.error(f"Error verifying file integrity for {dist_name} {version}: {e}")
            return {
                'distribution': dist_name,
                'version': version,
                'status': 'failed',
                'path': repo_path,
                'details': f'File integrity verification error: {e}',
                'gpg_verified': False,
                'checksums_verified': 0,
                'total_files_checked': 0
            }
    
    def _verify_apt_file_integrity(self, dist_name: str, version: str, dist_config: DistributionConfig, 
                                   repo_path: str, check_signatures: bool) -> Dict[str, Any]:
        """Verify APT repository file integrity with GPG and checksums"""
        details = []
        gpg_verified = False
        checksums_verified = 0
        total_files_checked = 0
        
        dists_path = os.path.join(repo_path, 'dists', version)
        if not os.path.exists(dists_path):
            return {
                'distribution': dist_name,
                'version': version,
                'status': 'missing',
                'path': repo_path,
                'details': f'Missing dists/{version} directory',
                'gpg_verified': False,
                'checksums_verified': 0,
                'total_files_checked': 0
            }
        
        # GPG signature verification for Release file
        if check_signatures:
            gpg_result = self._verify_apt_gpg_signature(dists_path, dist_name, version)
            gpg_verified = gpg_result['verified']
            if gpg_result['details']:
                details.append(gpg_result['details'])
        
        # SHA256 checksum verification for packages
        checksum_result = self._verify_apt_checksums(dists_path, repo_path, dist_config, dist_name)
        checksums_verified = checksum_result['verified_count']
        total_files_checked = checksum_result['total_count']
        if checksum_result['details']:
            details.extend(checksum_result['details'])
        
        # Package-level verification (.deb files)
        package_result = self._verify_apt_packages(dists_path, repo_path, dist_config, dist_name, version)
        packages_verified = package_result['verified_count']
        total_packages = package_result['total_count']
        if package_result['details']:
            details.extend(package_result['details'])
        
        # Determine overall status
        if checksums_verified == 0 and total_files_checked > 0:
            # If we have files to check but none verified, that's a failure
            status = 'failed'
        elif not gpg_verified and check_signatures:
            # GPG verification failed, but we might still have valid checksums
            if checksums_verified > 0:
                # We have verified checksums, so it's partially verified
                status = 'verified'
                if any('No public key' in detail for detail in details):
                    logger.warning(f"GPG verification failed due to missing public key, but checksums verified")
            else:
                status = 'failed'
        else:
            status = 'verified'
        
        return {
            'distribution': dist_name,
            'version': version,
            'status': status,
            'path': repo_path,
            'details': '; '.join(details) if details else 'File integrity verified',
            'gpg_verified': gpg_verified,
            'checksums_verified': checksums_verified,
            'total_files_checked': total_files_checked,
            'packages_verified': packages_verified,
            'total_packages': total_packages
        }
    
    def _verify_yum_file_integrity(self, dist_name: str, version: str, dist_config: DistributionConfig,
                                   repo_path: str, check_signatures: bool) -> Dict[str, Any]:
        """Verify YUM repository file integrity with GPG and checksums"""
        details = []
        gpg_verified = False
        checksums_verified = 0
        total_files_checked = 0
        
        # YUM repos have different structure per version/architecture
        configured_archs = dist_config.architectures or ['x86_64']
        available_architectures = self._get_available_architectures(dist_name, version, configured_archs)
        
        for arch in available_architectures:
            # Check common YUM repository structures
            possible_paths = [
                os.path.join(repo_path, version, 'BaseOS', arch, 'os'),
                os.path.join(repo_path, version, 'AppStream', arch, 'os'),
                os.path.join(repo_path, version, arch),
                os.path.join(repo_path, arch),
            ]
            
            for check_path in possible_paths:
                if os.path.exists(check_path):
                    repodata_path = os.path.join(check_path, 'repodata')
                    if os.path.exists(repodata_path):
                        # GPG signature verification for repomd.xml
                        if check_signatures:
                            gpg_result = self._verify_yum_gpg_signature(repodata_path, dist_name, version, arch)
                            if gpg_result['verified']:
                                gpg_verified = True
                            if gpg_result['details']:
                                details.append(gpg_result['details'])
                        
                        # SHA256 checksum verification
                        checksum_result = self._verify_yum_checksums(repodata_path, check_path)
                        checksums_verified += checksum_result['verified_count']
                        total_files_checked += checksum_result['total_count']
                        if checksum_result['details']:
                            details.extend(checksum_result['details'])
                    break
        
        # Determine overall status
        if checksums_verified == 0 and total_files_checked > 0:
            # If we have files to check but none verified, that's a failure
            status = 'failed'
        elif check_signatures and not gpg_verified and total_files_checked > 0:
            # GPG verification failed, but we might still have valid checksums
            if checksums_verified > 0:
                # We have verified checksums, so it's partially verified
                status = 'verified'
                if any('No GPG signature file found' in detail for detail in details):
                    logger.warning(f"GPG signatures not available for YUM repository, but checksums verified")
            else:
                status = 'failed'
        else:
            status = 'verified'
        
        return {
            'distribution': dist_name,
            'version': version,
            'status': status,
            'path': repo_path,
            'details': '; '.join(details) if details else 'File integrity verified',
            'gpg_verified': gpg_verified,
            'checksums_verified': checksums_verified,
            'total_files_checked': total_files_checked
        }
    
    def _verify_apt_gpg_signature(self, dists_path: str, dist_name: str, version: str) -> Dict[str, Any]:
        """Verify GPG signature for APT Release file"""
        release_file = os.path.join(dists_path, 'Release')
        release_gpg_file = os.path.join(dists_path, 'Release.gpg')
        inrelease_file = os.path.join(dists_path, 'InRelease')
        
        # Check for signature files
        if os.path.exists(inrelease_file):
            # Verify inline signature
            return self._verify_gpg_file(inrelease_file, f"{dist_name} {version} InRelease")
        elif os.path.exists(release_gpg_file) and os.path.exists(release_file):
            # Verify detached signature
            return self._verify_gpg_detached(release_file, release_gpg_file, f"{dist_name} {version} Release")
        else:
            return {
                'verified': False,
                'details': f'No GPG signature files found for {dist_name} {version}'
            }
    
    def _verify_yum_gpg_signature(self, repodata_path: str, dist_name: str, version: str, arch: str) -> Dict[str, Any]:
        """Verify GPG signature for YUM repomd.xml"""
        repomd_file = os.path.join(repodata_path, 'repomd.xml')
        repomd_asc_file = os.path.join(repodata_path, 'repomd.xml.asc')
        
        if os.path.exists(repomd_asc_file) and os.path.exists(repomd_file):
            return self._verify_gpg_detached(repomd_file, repomd_asc_file, f"{dist_name} {version} {arch} repomd.xml")
        else:
            return {
                'verified': False,
                'details': f'No GPG signature file found for {dist_name} {version} {arch} repomd.xml'
            }
    
    def _try_import_missing_gpg_key(self, dist_name: str, stderr: str) -> bool:
        """Try to import missing GPG key automatically"""
        if "No public key" not in stderr:
            return False
            
        # Extract key ID from stderr (e.g., "using RSA key 827C8569F2518CC677FECA1AED65462EC8D5E4C5")
        import re
        key_match = re.search(r'using \w+ key ([A-F0-9]+)', stderr)
        if not key_match:
            return False
            
        key_id = key_match.group(1)
        
        # Check if we have a URL for this key
        if dist_name not in self.gpg_keys:
            return False
            
        key_url = None
        for known_key_id, url in self.gpg_keys[dist_name].items():
            if key_id.endswith(known_key_id) or known_key_id.endswith(key_id):
                key_url = url
                break
                
        if not key_url:
            logger.warning(f"No known key URL for {dist_name} key {key_id}")
            return False
            
        try:
            logger.info(f"Attempting to import GPG key {key_id} for {dist_name} from {key_url}")
            
            # Download and import the key
            response = requests.get(key_url, timeout=30)
            response.raise_for_status()
            
            # Import the key
            result = subprocess.run(['gpg', '--import'], 
                                  input=response.text, 
                                  capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                logger.info(f"Successfully imported GPG key {key_id} for {dist_name}")
                return True
            else:
                logger.warning(f"Failed to import GPG key {key_id}: {result.stderr}")
                return False
                
        except Exception as e:
            logger.warning(f"Error importing GPG key {key_id} for {dist_name}: {e}")
            return False

    def _verify_gpg_file(self, file_path: str, description: str) -> Dict[str, Any]:
        """Verify a GPG signed file (inline signature)"""
        try:
            result = subprocess.run(['gpg', '--verify', file_path], 
                                  capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                return {
                    'verified': True,
                    'details': f'GPG signature verified for {description}'
                }
            else:
                stderr = result.stderr.strip()
                
                # Try to import missing key and retry verification
                if "No public key" in stderr:
                    # Extract distribution name from description
                    dist_name = description.split()[0].lower() if description else 'unknown'
                    
                    if self._try_import_missing_gpg_key(dist_name, stderr):
                        # Retry verification after importing key
                        retry_result = subprocess.run(['gpg', '--verify', file_path], 
                                                    capture_output=True, text=True, timeout=30)
                        if retry_result.returncode == 0:
                            return {
                                'verified': True,
                                'details': f'GPG signature verified for {description} (after importing key)'
                            }
                        else:
                            stderr = retry_result.stderr.strip()
                
                # Handle common GPG issues more gracefully
                if "No public key" in stderr:
                    return {
                        'verified': False,
                        'details': f'GPG verification failed for {description}: {stderr}'
                    }
                elif "Can't check signature" in stderr:
                    return {
                        'verified': False,
                        'details': f'GPG verification failed for {description}: {stderr}'
                    }
                else:
                    return {
                        'verified': False,
                        'details': f'GPG verification failed for {description}: {stderr}'
                    }
        except FileNotFoundError:
            return {
                'verified': False,
                'details': f'GPG verification error for {description}: gpg command not found'
            }
        except subprocess.TimeoutExpired:
            return {
                'verified': False,
                'details': f'GPG verification error for {description}: verification timeout'
            }
        except Exception as e:
            return {
                'verified': False,
                'details': f'GPG verification error for {description}: {str(e)}'
            }
    
    def _verify_gpg_detached(self, data_file: str, sig_file: str, description: str) -> Dict[str, Any]:
        """Verify a detached GPG signature"""
        try:
            result = subprocess.run(['gpg', '--verify', sig_file, data_file], 
                                  capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                return {
                    'verified': True,
                    'details': f'GPG signature verified for {description}'
                }
            else:
                stderr = result.stderr.strip()
                
                # Try to import missing key and retry verification
                if "No public key" in stderr:
                    # Extract distribution name from description
                    dist_name = description.split()[0].lower() if description else 'unknown'
                    
                    if self._try_import_missing_gpg_key(dist_name, stderr):
                        # Retry verification after importing key
                        retry_result = subprocess.run(['gpg', '--verify', sig_file, data_file], 
                                                    capture_output=True, text=True, timeout=30)
                        if retry_result.returncode == 0:
                            return {
                                'verified': True,
                                'details': f'GPG signature verified for {description} (after importing key)'
                            }
                        else:
                            stderr = retry_result.stderr.strip()
                
                # Handle common GPG issues more gracefully
                if "No public key" in stderr:
                    return {
                        'verified': False,
                        'details': f'GPG verification failed for {description}: {stderr}'
                    }
                elif "Can't check signature" in stderr:
                    return {
                        'verified': False,
                        'details': f'GPG verification failed for {description}: {stderr}'
                    }
                else:
                    return {
                        'verified': False,
                        'details': f'GPG verification failed for {description}: {stderr}'
                    }
        except FileNotFoundError:
            return {
                'verified': False,
                'details': f'GPG verification error for {description}: gpg command not found'
            }
        except subprocess.TimeoutExpired:
            return {
                'verified': False,
                'details': f'GPG verification error for {description}: verification timeout'
            }
        except Exception as e:
            return {
                'verified': False,
                'details': f'GPG verification error for {description}: {str(e)}'
            }
    
    def _is_optional_file(self, filename: str, dist_name: str) -> bool:
        """Check if a file is optional and can be missing without being an error"""
        optional_patterns = [
            # Translation files - often not present in mirrors
            'i18n/Translation-',
            # CNF (command-not-found) files - optional feature
            'cnf/Commands-',
            # Debian installer files for architectures that may not be mirrored
            'debian-installer/binary-',
            # DEP-11 metadata files - optional AppStream data
            'dep11/Components-',
            'dep11/icons-',
            # Contents files - these are large and often not mirrored or only in compressed form
            'Contents-',
            'contrib/Contents-',
            'non-free/Contents-',
            # Legacy architecture Contents files
            'Contents-powerpc', 'Contents-s390', 'Contents-sparc', 'Contents-alpha',
            'Contents-hppa', 'Contents-m68k', 'Contents-sh4', 'Contents-ia64',
            # Binary package files for architectures that may not be mirrored
            'binary-armel/',
            'binary-armhf/',
            'binary-ppc64el/',
            'binary-riscv64/',
            'binary-s390x/',
            # Source packages - often not mirrored in partial mirrors
            'source/Sources',
        ]
        
        # Additional Ubuntu-specific optional files
        if dist_name == 'ubuntu':
            optional_patterns.extend([
                # Ubuntu-specific optional files
                'restricted/cnf/',
                'restricted/debian-installer/',
                'restricted/dep11/',
                'restricted/i18n/',
                'universe/cnf/',
                'universe/debian-installer/',
                'universe/dep11/',
                'universe/i18n/',
                'multiverse/cnf/',
                'multiverse/debian-installer/',
                'multiverse/dep11/',
                'multiverse/i18n/',
            ])
        
        # Kali-specific optional files
        elif dist_name == 'kali':
            optional_patterns.extend([
                'non-free-firmware/',  # May not exist in older Kali versions
                'Contents-',  # Contents files are often missing
            ])
        
        return any(pattern in filename for pattern in optional_patterns)

    def _should_verify_file(self, filename: str, dist_config: DistributionConfig) -> bool:
        """Check if a file should be verified based on configuration (architectures, components, etc.)"""
        
        # Get configured architectures and components
        configured_archs = dist_config.architectures or ['amd64']
        configured_components = dist_config.components or ['main']
        
        # Always verify Release files and other metadata
        metadata_files = ['Release', 'InRelease', 'Release.gpg']
        if filename in metadata_files:
            return True
        
        # Check component filtering
        component_match = False
        for component in configured_components:
            if filename.startswith(f'{component}/') or filename.startswith(component):
                component_match = True
                break
        
        # If it's a component-specific file and we don't have that component configured, skip it
        known_components = ['main', 'contrib', 'non-free', 'restricted', 'universe', 'multiverse', 'non-free-firmware']
        if any(filename.startswith(f'{comp}/') for comp in known_components):
            if not component_match:
                return False
        
        # Check architecture filtering
        arch_match = True  # Default to True for non-architecture specific files
        
        # Comprehensive list of all known Debian/Ubuntu architectures (including legacy ones)
        all_known_archs = [
            'amd64', 'i386', 'arm64', 'armhf', 'armel', 'ppc64el', 'riscv64', 's390x', 
            'mips', 'mipsel', 'mips64el', 'ia64', 'kfreebsd-amd64', 'kfreebsd-i386', 'all',
            # Legacy architectures from older releases
            'powerpc', 's390', 'sparc', 'alpha', 'hppa', 'm68k', 'sh4',
            # Additional Ubuntu architectures
            'ppc64', 'arm', 'armel'
        ]
        
        for arch in all_known_archs:
            if f'binary-{arch}' in filename or f'Contents-{arch}' in filename:
                arch_match = arch in configured_archs
                break
        
        return arch_match

    def _verify_apt_checksums(self, dists_path: str, repo_path: str, dist_config: DistributionConfig, dist_name: str) -> Dict[str, Any]:
        """Verify SHA256 checksums for APT packages using Release file"""
        details = []
        verified_count = 0
        total_count = 0
        missing_optional_count = 0
        debug_missing_count = 0
        skipped_config_count = 0
        
        release_file = os.path.join(dists_path, 'Release')
        if not os.path.exists(release_file):
            return {
                'verified_count': 0,
                'total_count': 0,
                'details': ['No Release file found for checksum verification']
            }
        
        # Parse Release file for SHA256 checksums
        try:
            with open(release_file, 'r') as f:
                content = f.read()
                
            # Extract SHA256 section
            sha256_section = False
            for line in content.split('\n'):
                if line.startswith('SHA256:'):
                    sha256_section = True
                    continue
                elif line.startswith('SHA1:') or line.startswith('MD5Sum:'):
                    sha256_section = False
                    continue
                elif sha256_section and line.strip():
                    # Parse checksum line: " hash size filename"
                    parts = line.strip().split()
                    if len(parts) == 3:
                        expected_hash, size, filename = parts
                        
                        # Skip files that don't match our configuration
                        if not self._should_verify_file(filename, dist_config):
                            skipped_config_count += 1
                            logger.debug(f'Skipping file not in configuration: {filename}')
                            continue
                        
                        # In APT Release files, all paths are relative to the dists/<version> directory
                        file_path = os.path.join(dists_path, filename)
                        
                        total_count += 1
                        
                        # Check if file exists, or try compressed variants
                        actual_file_path = file_path
                        if os.path.exists(file_path):
                            file_found = True
                        else:
                            # Try common compressed variants
                            compressed_variants = [f"{file_path}.gz", f"{file_path}.xz", f"{file_path}.bz2"]
                            file_found = False
                            for variant in compressed_variants:
                                if os.path.exists(variant):
                                    actual_file_path = variant
                                    file_found = True
                                    break
                        
                        if file_found:
                            if actual_file_path == file_path:
                                # Uncompressed file matches Release file entry exactly
                                actual_hash = self._calculate_sha256(actual_file_path)
                                if actual_hash == expected_hash:
                                    verified_count += 1
                                else:
                                    details.append(f'Checksum mismatch for {filename}')
                            else:
                                # We found a compressed variant - need to decompress and verify
                                actual_hash = self._calculate_sha256_decompressed(actual_file_path)
                                if actual_hash == expected_hash:
                                    verified_count += 1
                                    logger.debug(f'Verified compressed file {os.path.basename(actual_file_path)} against uncompressed hash')
                                elif actual_hash:
                                    details.append(f'Checksum mismatch for {filename} (decompressed from {os.path.basename(actual_file_path)})')
                                else:
                                    logger.debug(f'Could not decompress {actual_file_path} for verification')
                        else:
                            # Check if this is an optional file first
                            if self._is_optional_file(filename, dist_name):
                                missing_optional_count += 1
                                # Don't treat optional files as errors - just log for debugging
                                logger.debug(f'Optional file missing: {filename}')
                            else:
                                # Only log warning for files we expect to exist (matching our config)
                                if debug_missing_count < 5:
                                    logger.warning(f'File not found ({dist_name}): {filename} -> {file_path}')
                                    debug_missing_count += 1
                                details.append(f'Missing file for checksum verification: {filename}')
        
        except Exception as e:
            details.append(f'Error parsing Release file for checksums: {e}')
        
        # Add summary of optional files if any were missing
        if missing_optional_count > 0:
            logger.info(f'Skipped {missing_optional_count} optional files that were missing')
        
        # Add summary of files skipped due to configuration
        if skipped_config_count > 0:
            logger.info(f'Skipped {skipped_config_count} files not matching configuration (architectures/components)')
        
        return {
            'verified_count': verified_count,
            'total_count': total_count,
            'details': details
        }
    
    def _verify_yum_checksums(self, repodata_path: str, repo_path: str) -> Dict[str, Any]:
        """Verify SHA256 checksums for YUM packages using repomd.xml"""
        details = []
        verified_count = 0
        total_count = 0
        
        repomd_file = os.path.join(repodata_path, 'repomd.xml')
        if not os.path.exists(repomd_file):
            return {
                'verified_count': 0,
                'total_count': 0,
                'details': ['No repomd.xml file found for checksum verification']
            }
        
        # Parse repomd.xml for checksums (simplified - would need full XML parsing for production)
        try:
            with open(repomd_file, 'r') as f:
                content = f.read()
                
            # Basic checksum verification for key metadata files
            import re
            checksum_matches = re.findall(r'checksum type="sha256">([a-f0-9]{64})</checksum>', content)
            location_matches = re.findall(r'location href="([^"]+)"', content)
            
            for i, (checksum, location) in enumerate(zip(checksum_matches, location_matches)):
                if i < len(checksum_matches):  # Ensure we don't go out of bounds
                    file_path = os.path.join(repodata_path, os.path.basename(location))
                    
                    total_count += 1
                    if os.path.exists(file_path):
                        actual_hash = self._calculate_sha256(file_path)
                        if actual_hash == checksum:
                            verified_count += 1
                        else:
                            details.append(f'Checksum mismatch for {os.path.basename(location)}')
                    else:
                        details.append(f'Missing file for checksum verification: {os.path.basename(location)}')
        
        except Exception as e:
            details.append(f'Error parsing repomd.xml for checksums: {e}')
        
        return {
            'verified_count': verified_count,
            'total_count': total_count,
            'details': details
        }
    
    def _calculate_sha256(self, file_path: str) -> str:
        """Calculate SHA256 hash of a file"""
        hash_sha256 = hashlib.sha256()
        try:
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_sha256.update(chunk)
            return hash_sha256.hexdigest()
        except Exception as e:
            logger.error(f"Error calculating SHA256 for {file_path}: {e}")
            return ""
    
    def _calculate_sha256_decompressed(self, file_path: str) -> str:
        """Calculate SHA256 hash of a compressed file's decompressed content"""
        hash_sha256 = hashlib.sha256()
        try:
            if file_path.endswith('.gz'):
                with gzip.open(file_path, 'rb') as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        hash_sha256.update(chunk)
            elif file_path.endswith('.xz'):
                import lzma
                with lzma.open(file_path, 'rb') as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        hash_sha256.update(chunk)
            elif file_path.endswith('.bz2'):
                import bz2
                with bz2.open(file_path, 'rb') as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        hash_sha256.update(chunk)
            else:
                logger.warning(f"Unknown compression format for {file_path}")
                return ""
            
            return hash_sha256.hexdigest()
        except Exception as e:
            logger.error(f"Error calculating SHA256 for decompressed {file_path}: {e}")
            return ""
    
    def _verify_apt_packages(self, dists_path: str, repo_path: str, dist_config: DistributionConfig, 
                            dist_name: str, version: str) -> Dict[str, Any]:
        """Verify individual .deb packages using SHA256 hashes from Packages files with parallel processing"""
        details = []
        verified_count = 0
        total_count = 0
        
        # Get architectures that were actually available for this distribution version
        configured_archs = dist_config.architectures or ['amd64']
        available_architectures = self._get_available_architectures(dist_name, version, configured_archs)
        
        # Collect all package verification tasks
        package_tasks = []
        
        for component in dist_config.components or ['main']:
            for arch in available_architectures:
                # Find Packages file (compressed or uncompressed)
                packages_path = None
                base_packages_path = os.path.join(dists_path, component, f'binary-{arch}', 'Packages')
                
                if os.path.exists(base_packages_path):
                    packages_path = base_packages_path
                elif os.path.exists(f'{base_packages_path}.gz'):
                    packages_path = f'{base_packages_path}.gz'
                elif os.path.exists(f'{base_packages_path}.xz'):
                    packages_path = f'{base_packages_path}.xz'
                elif os.path.exists(f'{base_packages_path}.bz2'):
                    packages_path = f'{base_packages_path}.bz2'
                
                if not packages_path:
                    logger.debug(f'No Packages file found for {component}/{arch}')
                    continue
                
                # Parse Packages file and add tasks
                try:
                    package_info = self._parse_packages_file(packages_path)
                    
                    for package in package_info:
                        if 'Filename' not in package or 'SHA256' not in package:
                            continue
                        
                        filename = package['Filename']
                        expected_sha256 = package['SHA256']
                        deb_path = os.path.join(repo_path, filename)
                        
                        package_tasks.append((deb_path, filename, expected_sha256))
                        total_count += 1
                
                except Exception as e:
                    details.append(f'Error parsing Packages file for {component}/{arch}: {e}')
                    logger.error(f'Error parsing {packages_path}: {e}')
        
        if not package_tasks:
            return {
                'verified_count': 0,
                'total_count': 0,
                'details': details
            }
        
        # Process packages in parallel (but use fewer workers to avoid overwhelming the system)
        max_workers = min(len(package_tasks), max(2, multiprocessing.cpu_count() // 2))
        
        logger.debug(f"Verifying {len(package_tasks)} packages for {dist_name} {version} using {max_workers} workers")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all package verification tasks
            future_to_package = {
                executor.submit(self._verify_package_checksum, deb_path, filename, expected_sha256): 
                (deb_path, filename, expected_sha256)
                for deb_path, filename, expected_sha256 in package_tasks
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_package):
                deb_path, filename, expected_sha256 = future_to_package[future]
                
                try:
                    verification_result = future.result()
                    if verification_result['verified']:
                        verified_count += 1
                        logger.debug(f'Verified package: {os.path.basename(filename)}')
                    else:
                        details.append(verification_result['error'])
                        if 'mismatch' in verification_result['error'].lower():
                            logger.warning(f"SHA256 mismatch for {filename}: expected {expected_sha256}")
                        
                except Exception as e:
                    details.append(f'Error verifying package {os.path.basename(filename)}: {e}')
                    logger.error(f'Error verifying {filename}: {e}')
        
        logger.debug(f"Package verification completed for {dist_name} {version}: {verified_count}/{total_count} verified")
        
        return {
            'verified_count': verified_count,
            'total_count': total_count,
            'details': details
        }
    
    def _verify_package_checksum(self, deb_path: str, filename: str, expected_sha256: str) -> Dict[str, Any]:
        """Verify a single package's SHA256 checksum"""
        if not os.path.exists(deb_path):
            return {
                'verified': False,
                'error': f'Missing package file: {filename}'
            }
        
        # Verify SHA256 checksum
        actual_sha256 = self._calculate_sha256(deb_path)
        if actual_sha256 == expected_sha256:
            return {
                'verified': True,
                'error': None
            }
        else:
            return {
                'verified': False,
                'error': f'Package checksum mismatch: {os.path.basename(filename)}'
            }
    
    def _parse_packages_file(self, packages_path: str) -> List[Dict[str, str]]:
        """Parse APT Packages file and extract package information"""
        packages = []
        current_package = {}
        
        try:
            # Handle compressed files
            if packages_path.endswith('.gz'):
                with gzip.open(packages_path, 'rt', encoding='utf-8') as f:
                    content = f.read()
            elif packages_path.endswith('.xz'):
                import lzma
                with lzma.open(packages_path, 'rt', encoding='utf-8') as f:
                    content = f.read()
            elif packages_path.endswith('.bz2'):
                import bz2
                with bz2.open(packages_path, 'rt', encoding='utf-8') as f:
                    content = f.read()
            else:
                with open(packages_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            
            # Parse the content
            lines = content.split('\n')
            for line in lines:
                line = line.strip()
                
                if not line:
                    # Empty line indicates end of package entry
                    if current_package:
                        packages.append(current_package)
                        current_package = {}
                    continue
                
                if ':' in line:
                    # New field
                    key, value = line.split(':', 1)
                    current_package[key.strip()] = value.strip()
                else:
                    # Continuation of previous field (multiline)
                    # For our purposes, we only care about single-line fields
                    pass
            
            # Don't forget the last package if file doesn't end with empty line
            if current_package:
                packages.append(current_package)
        
        except Exception as e:
            logger.error(f'Error parsing Packages file {packages_path}: {e}')
            return []
        
        return packages