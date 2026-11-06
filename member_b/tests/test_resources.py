from contextlib import ExitStack
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from shared.exceptions import ServiceError
from member_b.service.resources import load_resources
from shared.settings import load_settings


class ResourceTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(os.environ, {
            'OPENAI_API_KEY': 'test-openai-key',
            'QDRANT_URL': ' https://example.test ',
            'QDRANT_API_KEY': ' test-qdrant-key ',
        }, clear=True))
        stack.enter_context(patch('member_b.service.resources.load_dotenv'))
        stack.enter_context(patch('member_b.service.resources.Embedder', return_value=SimpleNamespace(dim=2)))
        self.tracer = stack.enter_context(patch('member_b.service.resources.Tracer.from_env')).return_value
        self.factory = stack.enter_context(patch('member_a.indexing.vector_store.QdrantClient'))
        self.client = self.factory.return_value
        self.client.collection_exists.return_value = True
        self.client.get_collection.return_value.config.params.vectors.size = 2
        self.openai = stack.enter_context(patch('member_b.service.resources.OpenAI'))
        stack.enter_context(patch('member_b.service.resources.ChatOpenAI'))
        stack.enter_context(patch('member_b.service.resources.tiktoken.get_encoding', return_value=SimpleNamespace(encode=lambda text: text)))
        self.settings = load_settings()

    def test_loads_A_index_with_existing_API_connection_options(self):
        resources = load_resources(self.settings)
        self.factory.assert_called_once_with(
            url='https://example.test', api_key='test-qdrant-key',
            timeout=20, check_compatibility=False,
        )
        self.client.collection_exists.assert_called_once_with(self.settings.qdrant_collection)
        self.client.get_collection.assert_called_once_with(self.settings.qdrant_collection)
        self.assertIs(resources.qdrant, self.client)
        self.assertEqual(resources.count_tokens('문맥'), 2)
        resources.close()
        self.client.close.assert_called_once()
        self.openai.return_value.close.assert_called_once()
        self.tracer.shutdown.assert_called_once()

    def test_missing_index_is_503_and_closes_the_opened_client(self):
        self.client.collection_exists.return_value = False
        with self.assertRaises(ServiceError) as caught:
            load_resources(self.settings)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn('검색 색인이 없습니다', str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, LookupError)
        self.client.close.assert_called_once()
        self.tracer.shutdown.assert_called_once()
        self.openai.assert_not_called()

    def test_failed_index_lookup_closes_the_opened_client(self):
        self.client.collection_exists.side_effect = RuntimeError('lookup failed')
        with self.assertRaises(RuntimeError):
            load_resources(self.settings)
        self.client.close.assert_called_once()
        self.tracer.shutdown.assert_called_once()
        self.openai.assert_not_called()

    def test_dimension_validation_is_preserved_and_closes_resources(self):
        self.client.get_collection.return_value.config.params.vectors.size = 3
        with self.assertRaises(ServiceError) as caught:
            load_resources(self.settings)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn('차원', str(caught.exception))
        self.client.close.assert_called_once()
        self.tracer.shutdown.assert_called_once()
        self.openai.assert_not_called()


if __name__ == '__main__':
    unittest.main()
