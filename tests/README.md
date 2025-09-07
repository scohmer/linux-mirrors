# Linux Mirrors Test Suite

This directory contains comprehensive test cases for the Linux repository mirroring system. The test suite is designed to catch bugs, verify functionality, and ensure system reliability across different environments and usage patterns.

## Test Structure

```
tests/
├── README.md                          # This file
├── conftest.py                        # Pytest configuration and shared fixtures
├── test_config_manager.py             # Configuration management tests
├── test_container_orchestrator.py     # Container orchestration tests  
├── test_sync_engines.py              # Repository sync engine tests
├── test_storage_manager.py           # Storage management tests
├── test_systemd_service_generator.py # Systemd service generation tests
├── test_main.py                       # Main application and CLI tests
├── test_integration.py               # Integration tests
└── test_end_to_end.py                # End-to-end workflow tests
```

## Test Categories

### Unit Tests
- **ConfigManager**: Configuration loading, validation, persistence
- **ContainerOrchestrator**: Container creation, management, cleanup
- **SyncEngines**: APT and YUM sync logic, command generation
- **StorageManager**: Directory management, cleanup, space monitoring
- **SystemdServiceGenerator**: Service file generation, scheduling
- **Main Application**: CLI parsing, command routing, error handling

### Integration Tests
- Component interaction testing
- Real filesystem operations
- Configuration persistence workflows
- Error handling across component boundaries

### End-to-End Tests
- Complete user workflows
- Command-line interface testing
- Multi-distribution sync scenarios
- Error recovery and resilience

## Test Markers

Tests are organized using pytest markers:

- `@pytest.mark.unit` - Unit tests (fast, isolated)
- `@pytest.mark.integration` - Integration tests (slower, filesystem I/O)
- `@pytest.mark.slow` - Slow-running tests (performance, stress tests)
- `@pytest.mark.container` - Tests requiring container runtime

## Running Tests

### Prerequisites

1. **Install test dependencies:**
   ```bash
   pip install -r requirements-test.txt
   ```

2. **Install main dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

### Basic Test Execution

```bash
# Run all tests
pytest

# Run with coverage report
pytest --cov=src --cov-report=html

# Run specific test categories
pytest -m unit                    # Unit tests only
pytest -m integration             # Integration tests only
pytest -m "not slow"              # Skip slow tests
pytest -m "not container"         # Skip container tests

# Run specific test files
pytest tests/test_config_manager.py
pytest tests/test_sync_engines.py

# Run specific test functions
pytest tests/test_config_manager.py::TestConfigManager::test_load_config_file_not_exists
```

### Advanced Test Options

```bash
# Parallel execution (faster)
pytest -n auto

# Verbose output
pytest -v

# Stop on first failure
pytest -x

# Run failed tests from last run
pytest --lf

# Show local variables in tracebacks
pytest -l --tb=long

# Run with specific log level
pytest --log-cli-level=DEBUG
```

### Container Tests

Some tests require a container runtime (podman or docker):

```bash
# Run container tests (requires podman/docker)
pytest -m container

# Skip container tests if runtime unavailable
pytest -m "not container"
```

## Test Environment

### Temporary Directories
Most tests use temporary directories that are automatically cleaned up:

```python
def test_example(temp_dir):
    # temp_dir is automatically created and cleaned up
    config_path = os.path.join(temp_dir, "config.yaml")
    # ... test logic
```

### Mock Fixtures
Common mock objects are available via fixtures:

```python
def test_example(mock_config_manager, sample_apt_distribution):
    # mock_config_manager provides a configured mock
    # sample_apt_distribution provides test data
    assert sample_apt_distribution.name == "debian"
```

### Environment Variables
Tests run with controlled environment variables to ensure consistency.

## Test Data and Fixtures

### Configuration Fixtures
- `sample_mirror_config` - Basic mirror configuration
- `sample_apt_distribution` - Debian-based distribution config
- `sample_yum_distribution` - Rocky Linux-based distribution config
- `sample_disabled_distribution` - Disabled distribution config

### Mock Fixtures
- `mock_config_manager` - Fully configured ConfigManager mock
- `mock_subprocess_run` - Controlled subprocess execution
- `sample_container_status` - Container status data
- `sample_storage_info` - Storage information data

### Utility Fixtures
- `temp_dir` - Temporary directory with cleanup
- `environment_variables` - Controlled test environment
- `assert_helpers` - Custom assertion helpers

## Writing New Tests

### Test Structure

```python
import pytest
from unittest.mock import Mock, patch

from src.module_to_test import ClassToTest

class TestClassToTest:
    """Test ClassToTest functionality"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.test_data = "example"
    
    def teardown_method(self):
        """Clean up after tests"""
        pass
    
    def test_basic_functionality(self):
        """Test basic functionality"""
        # Arrange
        instance = ClassToTest()
        
        # Act
        result = instance.method_to_test()
        
        # Assert
        assert result == expected_value
    
    @patch('src.module_to_test.external_dependency')
    def test_with_mocking(self, mock_dependency):
        """Test with external dependencies mocked"""
        mock_dependency.return_value = "mocked_result"
        
        instance = ClassToTest()
        result = instance.method_that_uses_dependency()
        
        assert result == "expected_with_mock"
        mock_dependency.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_async_functionality(self):
        """Test asynchronous functionality"""
        instance = ClassToTest()
        result = await instance.async_method()
        assert result is not None
```

### Best Practices

1. **Descriptive Test Names**: Use clear, descriptive test method names
2. **Arrange-Act-Assert**: Structure tests clearly
3. **One Assertion Per Test**: Focus each test on one specific behavior
4. **Mock External Dependencies**: Isolate units under test
5. **Use Fixtures**: Leverage pytest fixtures for common setup
6. **Test Edge Cases**: Include boundary conditions and error cases
7. **Document Complex Tests**: Add docstrings for complex test logic

### Mocking Guidelines

```python
# Mock external services/commands
@patch('subprocess.run')
def test_container_operation(self, mock_subprocess):
    mock_subprocess.return_value = Mock(returncode=0, stdout="success")
    # ... test logic

# Mock filesystem operations for unit tests
@patch('os.path.exists', return_value=True)
@patch('builtins.open', mock_open(read_data="config data"))
def test_file_operations(self, mock_open, mock_exists):
    # ... test logic

# Use real filesystem for integration tests
def test_real_file_operations(self, temp_dir):
    config_path = os.path.join(temp_dir, "config.yaml")
    # ... test with real files
```

## Debugging Tests

### Common Issues

1. **Import Errors**: Ensure `src/` is in Python path
2. **Fixture Not Found**: Check `conftest.py` for available fixtures
3. **Mock Not Working**: Verify patch target paths
4. **Temporary Files**: Use `temp_dir` fixture for file operations
5. **Async Tests**: Use `@pytest.mark.asyncio` decorator

### Debug Techniques

```python
# Add debug output
def test_debug_example(self, capfd):
    print("Debug information")  # Will be captured
    # ... test logic
    
    captured = capfd.readouterr()
    print(f"Captured output: {captured.out}")

# Use pytest fixtures for debugging
def test_with_debug(self, temp_dir, capsys):
    print(f"Using temp directory: {temp_dir}")
    # ... test logic

# Break into debugger
def test_with_pdb(self):
    import pdb; pdb.set_trace()  # Debugger breakpoint
    # ... test logic
```

### Running Specific Tests with Debug Info

```bash
# Show debug output
pytest -s tests/test_specific.py::test_function

# Show all output including passed tests
pytest -s -v

# Show failed test details
pytest --tb=long

# Drop into debugger on failures
pytest --pdb
```

## Continuous Integration

Tests are designed to run in CI environments:

- **Fast Execution**: Unit tests complete quickly
- **No External Dependencies**: Container tests are properly marked
- **Deterministic**: Tests produce consistent results
- **Clean Environment**: Tests don't leave artifacts

### CI Configuration Example

```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.9'
      - run: pip install -r requirements-test.txt
      - run: pytest -m "not container" --cov=src
      - run: pytest -m container  # Only if container runtime available
```

## Test Coverage

Aim for high test coverage across all components:

- **Unit Tests**: 90%+ line coverage for individual modules
- **Integration Tests**: Cover component interactions
- **End-to-End Tests**: Cover complete user workflows

### Coverage Reports

```bash
# Generate HTML coverage report
pytest --cov=src --cov-report=html
open htmlcov/index.html

# Terminal coverage report
pytest --cov=src --cov-report=term-missing

# Coverage for specific modules
pytest --cov=src.config.manager --cov-report=term
```

## Performance Testing

Some tests verify performance characteristics:

```python
@pytest.mark.slow
def test_large_config_performance(self):
    """Test performance with large configurations"""
    start_time = time.time()
    # ... performance test logic
    duration = time.time() - start_time
    assert duration < 5.0, f"Operation too slow: {duration}s"
```

Run performance tests separately:

```bash
pytest -m slow  # Run only slow/performance tests
```

## Troubleshooting

### Common Test Failures

1. **File Not Found**: Check file paths and temp directory usage
2. **Permission Denied**: Verify test runs with appropriate permissions
3. **Mock Assertion Failures**: Check mock call arguments and counts
4. **Async Test Failures**: Ensure proper async/await usage
5. **Container Tests Failing**: Verify container runtime availability

### Getting Help

1. Check test output and tracebacks carefully
2. Run individual failing tests with `-v` for more detail
3. Use `--pdb` to debug interactively
4. Review test documentation and examples
5. Check that all dependencies are installed

## Contributing

When contributing new tests:

1. Follow existing test patterns and naming conventions
2. Add appropriate markers (`@pytest.mark.unit`, etc.)
3. Include docstrings for complex tests
4. Ensure tests are deterministic and isolated
5. Add any new fixtures to `conftest.py`
6. Update this documentation if adding new test categories

## Examples

See individual test files for examples of:
- Unit testing with mocks
- Integration testing with real filesystems
- Async testing patterns
- Error condition testing
- Performance testing
- End-to-end workflow testing