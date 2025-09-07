# Linux Mirrors - Development and Testing Makefile

.PHONY: help install install-dev test test-unit test-integration test-e2e test-container test-coverage lint type-check format clean build docs setup-dev

# Default target
help:
	@echo "Linux Mirrors - Available Commands:"
	@echo ""
	@echo "Development Setup:"
	@echo "  setup-dev          - Set up development environment"
	@echo "  install            - Install package dependencies"
	@echo "  install-dev        - Install development dependencies"
	@echo ""
	@echo "Testing:"
	@echo "  test               - Run all tests"
	@echo "  test-unit          - Run unit tests only"
	@echo "  test-integration   - Run integration tests only"
	@echo "  test-e2e           - Run end-to-end tests only"
	@echo "  test-container     - Run container tests only"
	@echo "  test-coverage      - Run tests with coverage report"
	@echo "  test-fast          - Run fast tests (skip slow/container)"
	@echo ""
	@echo "Code Quality:"
	@echo "  lint               - Run linting checks"
	@echo "  type-check         - Run type checking"
	@echo "  format             - Format code"
	@echo "  format-check       - Check code formatting"
	@echo ""
	@echo "Build & Package:"
	@echo "  build              - Build package"
	@echo "  clean              - Clean build artifacts"
	@echo ""
	@echo "Documentation:"
	@echo "  docs               - Generate documentation"
	@echo ""
	@echo "Container Operations:"
	@echo "  container-check    - Check container runtime availability"
	@echo "  container-build    - Build test containers"
	@echo ""

# Development setup
setup-dev: install-dev
	@echo "Development environment set up successfully!"

install:
	pip install -r requirements.txt

install-dev: install
	pip install -r requirements-test.txt
	pip install black flake8 mypy isort pre-commit
	pip install -e .
	pre-commit install

# Testing targets
test:
	pytest

test-unit:
	pytest -m unit

test-integration:
	pytest -m integration

test-e2e:
	pytest tests/test_end_to_end.py

test-container:
	pytest -m container

test-coverage:
	pytest --cov=src --cov-report=html --cov-report=term-missing

test-coverage-xml:
	pytest --cov=src --cov-report=xml

test-fast:
	pytest -m "not slow and not container"

test-parallel:
	pytest -n auto

test-verbose:
	pytest -v

test-debug:
	pytest -s --tb=long

# Test with specific markers
test-slow:
	pytest -m slow

test-quick:
	pytest -x --ff

# Code quality
lint:
	flake8 src/ tests/
	
type-check:
	mypy src/

format:
	black src/ tests/
	isort src/ tests/

format-check:
	black --check src/ tests/
	isort --check-only src/ tests/

quality-check: lint type-check format-check
	@echo "All quality checks passed!"

# Build and packaging
build: clean
	python -m build

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/

# Documentation
docs:
	@echo "Generating documentation..."
	@echo "Documentation files:"
	@echo "  - README.md (main)"
	@echo "  - CLAUDE.md (project specifications)"
	@echo "  - tests/README.md (test documentation)"

# Container operations
container-check:
	@echo "Checking container runtime availability..."
	@if command -v podman >/dev/null 2>&1; then \
		echo "✓ Podman available: $$(podman --version)"; \
	elif command -v docker >/dev/null 2>&1; then \
		echo "✓ Docker available: $$(docker --version)"; \
	else \
		echo "⚠ No container runtime found (podman or docker required for container tests)"; \
		exit 1; \
	fi

container-build: container-check
	@echo "Building test container images..."
	# This would build any test-specific containers if needed

# Development utilities
dev-setup-git-hooks:
	pre-commit install --hook-type pre-commit
	pre-commit install --hook-type pre-push

dev-run-hooks:
	pre-commit run --all-files

# CI/CD helpers
ci-test: test-coverage-xml
	@echo "CI tests completed with coverage"

ci-quality: quality-check
	@echo "CI quality checks completed"

ci-full: ci-quality ci-test
	@echo "Full CI pipeline completed"

# Debug and utility targets
debug-env:
	@echo "Python version: $$(python --version)"
	@echo "Python path: $$(which python)"
	@echo "Pip version: $$(pip --version)"
	@echo "Pytest version: $$(pytest --version)"
	@echo "Current directory: $$(pwd)"
	@echo "Environment: $$(env | grep -E '(VIRTUAL_ENV|CONDA)' || echo 'No virtual environment detected')"

debug-deps:
	pip list | grep -E "(pytest|textual|pyyaml|psutil|requests)"

# Example usage targets
example-run-sync:
	python -m src.main sync --help

example-run-status:
	python -m src.main status --help

example-config:
	@echo "Example configuration locations:"
	@echo "  System: /etc/linux-mirrors/config.yaml"
	@echo "  User: ~/.config/linux-mirrors/config.yaml"
	@echo "  Custom: --config /path/to/config.yaml"

# Performance testing
perf-test:
	pytest -m slow --tb=short

benchmark:
	@echo "Running performance benchmarks..."
	pytest tests/test_integration.py::TestPerformanceIntegration -v

# Test data and fixtures
test-fixtures:
	@echo "Available test fixtures:"
	@echo "  - temp_dir: Temporary directory with cleanup"
	@echo "  - mock_config_manager: Configured ConfigManager mock"
	@echo "  - sample_*_distribution: Test distribution configs"
	@echo "  - sample_mirror_config: Test mirror configuration"
	@echo "  - environment_variables: Controlled test environment"

# Maintenance
check-deps:
	pip check
	pip list --outdated

update-deps:
	pip install --upgrade pip setuptools wheel
	pip install --upgrade -r requirements.txt
	pip install --upgrade -r requirements-test.txt

# Security
security-check:
	pip install safety
	safety check

# Release helpers
pre-release: clean quality-check test-coverage
	@echo "Pre-release checks completed successfully!"
	@echo "Ready for release preparation."

# Install in development mode
dev-install:
	pip install -e .

# Uninstall development installation
dev-uninstall:
	pip uninstall linux-mirrors