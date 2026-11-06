from dataclasses import fields
from fnmatch import fnmatchcase
from pathlib import Path
import subprocess
import sys
import tomllib
import unittest

import app.generation.context as context
from app.generation.chain import DEFAULT_PROMPT
from shared import contracts
from shared.settings import DEFAULT_CONFIG, PROJECT_ROOT, load_settings


class PackageLayoutTests(unittest.TestCase):
    def test_B_uses_shared_contracts_and_local_paths(self):
        self.assertIs(context.RetrievedChunk, contracts.RetrievedChunk)
        self.assertEqual([field.name for field in fields(contracts.Chunk)], [
            'chunk_id', 'book_id', 'file_name', 'page', 'text', 'heading',
        ])
        self.assertEqual(PROJECT_ROOT, Path(__file__).resolve().parents[1])
        self.assertTrue(DEFAULT_CONFIG.is_file())
        self.assertTrue(DEFAULT_PROMPT.is_file())
        self.assertTrue(load_settings().documents_dir.is_absolute())

    def test_B_and_main_import_when_A_C_packages_are_unavailable(self):
        code = (
            "import sys; sys.modules.update({name:None for name in "
            "('app.indexing','app.retrieval','app.observability')}); "
            "import main, app.service.query, app.generation.chain; "
            "assert main.app.state.query_handler is None"
        )
        result = subprocess.run([sys.executable, '-c', code], cwd=PROJECT_ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_package_discovery_does_not_require_A_C_sources(self):
        with (PROJECT_ROOT / 'pyproject.toml').open('rb') as stream:
            config = tomllib.load(stream)
        include = config['tool']['setuptools']['packages']['find']['include']
        for name in ('shared', 'shared.contracts', 'app', 'app.api', 'app.generation', 'app.service'):
            self.assertTrue(any(fnmatchcase(name, pattern) for pattern in include))
        self.assertEqual(config['tool']['setuptools']['package-data']['app.generation'], ['prompts/*.txt'])


if __name__ == '__main__':
    unittest.main()
