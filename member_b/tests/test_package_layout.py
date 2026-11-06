from dataclasses import fields
from pathlib import Path
import tomllib
import unittest

import member_a.retrieval.dense as dense
import member_b.generation.context as context
from member_b.generation.chain import DEFAULT_PROMPT
from shared import contracts
from shared.settings import DEFAULT_CONFIG, PROJECT_ROOT, load_settings


class PackageLayoutTests(unittest.TestCase):
    def test_A_and_B_use_the_same_shared_types(self):
        self.assertIs(dense.Chunk, contracts.Chunk)
        self.assertIs(dense.RetrievedChunk, contracts.RetrievedChunk)
        self.assertIs(context.RetrievedChunk, contracts.RetrievedChunk)
        self.assertEqual([field.name for field in fields(contracts.Chunk)], [
            'chunk_id', 'book_id', 'file_name', 'page', 'text', 'heading',
        ])
        for name in ('PageDocument', 'Citation', 'ContextBundle', 'ModelAnswer',
                     'GenerationResult', 'QueryRequest', 'QueryResponse'):
            self.assertTrue(hasattr(contracts, name))

    def test_config_and_prompt_paths_survive_the_move(self):
        self.assertEqual(PROJECT_ROOT, Path(__file__).resolve().parents[2])
        self.assertTrue(DEFAULT_CONFIG.is_file())
        self.assertEqual(DEFAULT_PROMPT, PROJECT_ROOT / 'member_b/prompts/shipping_qa.txt')
        self.assertTrue(DEFAULT_PROMPT.is_file())
        self.assertTrue(load_settings().documents_dir.is_absolute())

    def test_installation_discovers_all_member_packages(self):
        with (PROJECT_ROOT / 'pyproject.toml').open('rb') as stream:
            config = tomllib.load(stream)
        include = config['tool']['setuptools']['packages']['find']['include']
        for name in ('shared', 'member_a', 'member_b', 'member_c'):
            self.assertIn(name, include)
            self.assertIn(name + '.*', include)
        self.assertNotIn('app*', include)
        self.assertEqual(config['tool']['setuptools']['package-data']['member_b'], ['prompts/*.txt'])


if __name__ == '__main__':
    unittest.main()
