"""Allow ``python3 -m unittest tests.agent_selection`` to run the package.

``unittest`` does not discover submodules when given a package name, so this
package registers its own modules explicitly. ``discover -s tests`` picks the
same modules up through the ``test_*.py`` naming.
"""
import importlib

_TEST_MODULES = ('test_scope', 'test_local_store', 'test_runtime_adapter',
                 'test_discovery', 'test_roles', 'test_model_selection',
                 'test_model_identity', 'test_feedback', 'test_e2e',
                 'test_module_registry',
                 'model_sources.test_snapshot',
                 'model_sources.test_artificial_analysis')


def load_tests(loader, standard_tests, pattern):
    for name in _TEST_MODULES:
        module = importlib.import_module(f'{__name__}.{name}')
        standard_tests.addTests(loader.loadTestsFromModule(module))
    return standard_tests
