from dataclasses import asdict
from hashlib import sha256
import unittest

from shared.contracts import Chunk, RetrievedChunk
from member_chsopo89.generation.citations import resolve_citations
from member_chsopo89.generation.context import build_context


def candidate(number, text):
    return RetrievedChunk(
        chunk=Chunk(
            chunk_id=f"DEMO-p{number}-c1",
            book_id="DEMO",
            file_name="DEMO.pdf",
            page=number,
            text=text,
            heading="예약 안내",
        ),
        rank=number,
        score=1.0 / number,
        retriever="dense",
    )


class ContextTests(unittest.TestCase):
    def test_final_combined_text_stays_within_budget_for_non_additive_counter(self):
        first, second = candidate(1, "첫 자료"), candidate(2, "다음 자료")

        def counter(text):
            return 1000 if "[S2]" in text and "[S1]" in text else len(text)

        context = build_context([first, second], 2, 500, counter)
        self.assertEqual(context.final_chunks, [first])
        self.assertLessEqual(context.token_count, 500)
        self.assertEqual(list(context.source_map), ["S1"])

    def test_selects_whole_chunks_with_source_overhead(self):
        short = candidate(1, "예약 접수와 확정은 별도입니다.")
        reference = build_context([short], 1, 10000, len)
        oversized = candidate(2, "가" * (len(reference.text) + 1))
        context = build_context([oversized, short, short], 3, len(reference.text), len)

        self.assertEqual(context.final_chunks, [short])
        self.assertEqual(context.text, reference.text)
        self.assertEqual(context.token_count, len(context.text))
        self.assertEqual(context.source_map["S1"].page, 1)
        self.assertIn("제목: 예약 안내", context.text)
        self.assertEqual(context.context_hash, sha256(context.text.encode()).hexdigest())

    def test_k_and_source_ids_follow_selected_order(self):
        first, second = candidate(1, "첫 자료"), candidate(2, "다음 자료")
        context = build_context([first, first, second], 2, 10000, len)
        self.assertEqual(context.final_chunks, [first, second])
        self.assertEqual(list(context.source_map), ["S1", "S2"])
        self.assertEqual(asdict(context.final_chunks[0].chunk)["heading"], "예약 안내")
        self.assertEqual(context.model_dump()["final_chunks"][1]["chunk"]["page"], 2)

    def test_budget_counts_each_block_once_including_separator(self):
        first, second = candidate(1, "첫 자료"), candidate(2, "다음 자료")
        both = build_context([first, second], 2, 10000, len)
        exact = build_context([first, second], 2, len(both.text), len)
        short = build_context([first, second], 2, len(both.text) - 1, len)
        self.assertEqual(exact.final_chunks, [first, second])
        self.assertEqual(short.final_chunks, [first])

        counted = []
        def counter(text):
            counted.append(text)
            return len(text)
        build_context([first, second], 2, 10000, counter)
        self.assertNotIn(both.text, counted[:-1])

    def test_budget_excluded_chunks_are_recorded_without_duplicates(self):
        first, second = candidate(1, "첫 자료"), candidate(2, "다음 자료")
        reference = build_context([first], 1, 10000, len)
        context = build_context([first, first, second], 2, len(reference.text), len)
        self.assertEqual(context.final_chunks, [first])
        self.assertEqual(context.excluded_chunk_ids, [second.chunk.chunk_id])
        self.assertEqual(build_context([first, second], 2, 10000, len).excluded_chunk_ids, [])

    def test_chunks_dropped_by_final_recount_are_recorded(self):
        first, second = candidate(1, "첫 자료"), candidate(2, "다음 자료")

        def counter(text):
            return 1000 if "[S2]" in text and "[S1]" in text else len(text)

        context = build_context([first, second], 2, 500, counter)
        self.assertEqual(context.final_chunks, [first])
        self.assertEqual(context.excluded_chunk_ids, [second.chunk.chunk_id])

    def test_empty_context_and_unknown_citations(self):
        context = build_context([], 5, 4000, len)
        self.assertEqual(context.text, "")
        self.assertEqual(resolve_citations([], context.source_map), [])
        with self.assertRaisesRegex(ValueError, "S1"):
            resolve_citations(["S1"], context.source_map)
        with self.assertRaises(ValueError):
            build_context([], 0, 4000, len)

    def test_citations_use_only_final_context_and_remove_duplicates(self):
        context = build_context([candidate(1, "문서 근거")], 1, 10000, len)
        result = resolve_citations(["S1", "S1"], context.source_map)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].file_name, "DEMO.pdf")
        self.assertEqual(result[0].page, 1)
        with self.assertRaisesRegex(ValueError, "S2"):
            resolve_citations(["S1", "S2"], context.source_map)


if __name__ == "__main__":
    unittest.main()
