from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from member_b.api.app import create_app
from shared.contracts import Chunk, ModelAnswer, RetrievedChunk


class QueryPipelineTests(unittest.TestCase):
    def test_search_result_reaches_B_model_answer_and_citation(self):
        chunk = Chunk('DEMO-p1-c1', 'DEMO', 'DEMO.pdf', 1, '예약 접수와 확정은 별도입니다.', '예약 안내')
        model = Mock(model_name='test-model', max_tokens=2048)
        model.with_structured_output.return_value.invoke.return_value = {
            'parsed': ModelAnswer(
                status='answered', answer='접수와 확정은 다릅니다. [S1]',
                reason='제공한 문서에서 확인했습니다.', source_ids=['S1'],
            ),
            'parsing_error': None,
            'raw': AIMessage(content='test output'),
        }
        resources = SimpleNamespace(
            model=model, count_tokens=len, close=Mock(), tracer=None, context_hasher=None,
            retriever=Mock(return_value=[RetrievedChunk(chunk, 1, 1.0, 'fake')]),
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
        self.assertIn(chunk.text, messages[1].content)
        self.assertEqual(result['request_id'], response.headers['X-Request-ID'])
        resources.retriever.assert_called_once()
        resources.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
