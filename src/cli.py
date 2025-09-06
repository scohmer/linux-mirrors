#!/usr/bin/env python3

"""
Command-line interface wrapper for linux-mirrors.

This module serves as the entry point for the CLI command and handles
proper package imports when installed via pip.
"""

def main():
    """Entry point for the linux-mirrors CLI command."""
    from .main import main as main_func
    return main_func()

if __name__ == "__main__":
    main()