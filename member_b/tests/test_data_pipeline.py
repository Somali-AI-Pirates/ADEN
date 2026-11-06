from contextlib import closing
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from qdrant_client import QdrantClient

from member_b.api.app import create_app
from shared.contracts import Chunk, ModelAnswer, PageDocument
from member_a.indexing.vector_store import build_vector_index
from member_a.ingestion.chunker import chunk_pages, index_text
from member_a.ingestion.loader import extract_pages
from member_a.retrieval.service import retrieve


class DataPipelineTests(unittest.TestCase):
    def test_loader_keeps_page_numbers_and_reports_failed_and_empty_pages(self):
        reader = SimpleNamespace(pages=[
            SimpleNamespace(extract_text=lambda: 'page one'),
            SimpleNamespace(extract_text=lambda: ''),
        ])
        with patch('member_a.ingestion.loader.PdfReader', side_effect=[reader, ValueError('invalid PDF')]):
            pages, report = extract_pages(['DEMO.pdf', 'BROKEN.pdf'])
        self.assertEqual([(page.book_id, page.page) for page in pages], [('DEMO', 1), ('DEMO', 2)])
        self.assertEqual(report['empty_pages'], ['DEMO p2'])
        self.assertEqual(report['failed'][0]['file_name'], 'BROKEN.pdf')

    def test_chunker_keeps_source_pages_and_headings(self):
        text = 'DEMO | Team\nBooking guide\nConfirmation\nFirst statement.\nSecond statement.\n1 / 2'
        pages = [PageDocument('DEMO', 'DEMO.pdf', page, text) for page in (1, 2)]
        chunks = chunk_pages(pages, max_chars=20)
        self.assertEqual(len(chunks), 4)
        self.assertEqual([chunk.page for chunk in chunks], [1, 1, 2, 2])
        self.assertEqual(len({chunk.chunk_id for chunk in chunks}), 4)
        self.assertIn('Booking guide > Confirmation', chunks[0].heading)
        self.assertNotIn('1 / 2', chunks[0].text)
        self.assertIn(chunks[0].heading, index_text(chunks[0]))

    def test_local_index_round_trip_and_existing_collection_guard(self):
        chunks = [
            Chunk('DEMO-p1-c1', 'DEMO', 'DEMO.pdf', 1, 'Booking received.', 'Booking'),
            Chunk('DEMO-p2-c1', 'DEMO', 'DEMO.pdf', 2, 'Booking confirmed.', 'Confirmation'),
        ]
        embedder = SimpleNamespace(
            dim=2,
            embed_passages=lambda texts: np.eye(len(texts), dtype=np.float32),
            embed_query=lambda text: np.array([1.0, 0.0], dtype=np.float32),
        )
        with closing(QdrantClient(':memory:')) as client:
            build_vector_index(client, 'test-data-pipeline', chunks, embedder)
            resources = SimpleNamespace(embedder=embedder, qdrant=client, collection='test-data-pipeline')
            results = retrieve('test question', 'naive', resources, top_n=1)
            self.assertEqual(results[0].chunk, chunks[0])
            with self.assertRaises(FileExistsError):
                build_vector_index(client, 'test-data-pipeline', chunks, embedder)
            self.assertEqual(client.count('test-data-pipeline', exact=True).count, 2)

    def test_A_chunks_reach_B_query_answer_and_PDF_citation(self):
        page = PageDocument('DEMO', 'DEMO.pdf', 1,
            'DEMO | Team\nBooking guide\nConfirmation\nBooking received is not confirmed.\n1 / 1')
        chunks = chunk_pages([page])
        embedder = SimpleNamespace(
            dim=2,
            embed_passages=lambda texts: np.array([[1.0, 0.0]], dtype=np.float32),
            embed_query=lambda text: np.array([1.0, 0.0], dtype=np.float32),
        )
        model = Mock(model_name='test-model', max_tokens=2048)
        model.with_structured_output.return_value.invoke.return_value = {
            'parsed': ModelAnswer(
                status='answered', answer='접수와 확정은 다릅니다. [S1]',
                reason='제공한 문서에서 확인했습니다.', source_ids=['S1'],
            ),
            'parsing_error': None,
            'raw': AIMessage(content='test output'),
        }
        with closing(QdrantClient(':memory:')) as qdrant:
            collection = 'basic_multilingual-e5-small_p800'
            build_vector_index(qdrant, collection, chunks, embedder)
            resources = SimpleNamespace(
                embedder=embedder, qdrant=qdrant, collection=collection,
                model=model, count_tokens=len, close=Mock(),
            )
            with (
                patch('member_b.api.app.load_resources', return_value=resources),
                TestClient(create_app()) as client,
            ):
                self.assertTrue(client.get('/health').json()['query_ready'])
                response = client.post('/query', json={'question': '접수하면 확정인가요?'})
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result['status'], 'answered')
            self.assertEqual(result['citations'], [{
                'book_id': 'DEMO', 'file_name': 'DEMO.pdf', 'page': 1, 'source_id': 'S1',
            }])
            messages = model.with_structured_output.return_value.invoke.call_args.args[0]
            self.assertIn(chunks[0].text, messages[1].content)
            self.assertEqual(result['request_id'], response.headers['X-Request-ID'])
            resources.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
