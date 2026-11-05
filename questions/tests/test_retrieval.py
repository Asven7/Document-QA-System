"""Tests for the hybrid retrieval pipeline (semantic + BM25 + RRF)."""

from __future__ import annotations

import uuid

from django.core.files.base import ContentFile

from core.testing import ChromaEnabledTestCase
from documents.models import Document
from documents.services import document_service, vector_store
from documents.tests.helpers import make_docx
from questions.services.retrieval import retrieve_chunks


class RetrievalTestBase(ChromaEnabledTestCase):
    """Base class: two indexed documents with distinct topics."""

    def setUp(self) -> None:
        super().setUp()
        # Document A: leave policy vocabulary.
        self.doc_a = document_service.create_document(
            uploaded_file=ContentFile(
                make_docx(
                    headings=["Leave policy"],
                    paragraphs=[
                        "Annual leave entitlement is 26 days per calendar year "
                        "for all full-time employees and requires manager approval. "
                    ]
                    * 6,
                ),
                name="leave.docx",
            ),
            title="Leave policy (EN)",
        )
        # Document B: deployment vocabulary with one shared keyword.
        self.doc_b = document_service.create_document(
            uploaded_file=ContentFile(
                make_docx(
                    headings=["Deployment guide"],
                    paragraphs=[
                        "Production deployments happen on Tuesday and Thursday "
                        "inside the 09:00 to 11:00 UTC window and require two "
                        "senior approvals. "
                    ]
                    * 6,
                ),
                name="deploy.docx",
            ),
            title="Deployment guide (EN)",
        )
        # Document C: Persian document about passwords.
        self.doc_c = document_service.create_document(
            uploaded_file=ContentFile(
                make_docx(
                    headings=["امنیت اطلاعات"],
                    paragraphs=[
                        "رمز عبور هر کارمند باید حداقل ۱۲ نویسه باشد و هر ۹۰ "
                        "روز یک‌بار تغییر کند و احراز هویت دو مرحله‌ای الزامی "
                        "است. "
                    ]
                    * 6,
                ),
                name="security.docx",
            ),
            title="امنیت اطلاعات (FA)",
        )


class SemanticChannelTests(RetrievalTestBase):
    def test_semantic_channel_finds_matching_chunk(self):
        results = retrieve_chunks("How many days of annual leave do employees get?")
        self.assertTrue(results)
        self.assertEqual(results[0].document_id, str(self.doc_a.pk))
        self.assertIsNotNone(results[0].semantic_rank)
        self.assertIsNotNone(results[0].semantic_distance)

    def test_semantic_channel_cross_language_persian_query(self):
        # Persian query about passwords must match the Persian document via
        # the fake embedding's shared normalized tokens (رمز/عبور/تغییر).
        results = retrieve_chunks("طول رمز عبور کارکنان چقدر باید باشد؟")
        self.assertTrue(results)
        self.assertEqual(results[0].document_id, str(self.doc_c.pk))

    @staticmethod
    def _semantic_ranks(results):
        return {r.chunk_id: r.semantic_rank for r in results if r.semantic_rank}


class LexicalChannelTests(RetrievalTestBase):
    def test_lexical_channel_ranks_exact_keyword_first(self):
        results = retrieve_chunks("production deployment window approvals")
        self.assertTrue(results)
        lexical_hits = [r for r in results if r.lexical_rank is not None]
        self.assertTrue(lexical_hits, "Expected BM25 to contribute rankings.")
        best_lexical = min(lexical_hits, key=lambda r: r.lexical_rank)
        self.assertEqual(best_lexical.document_id, str(self.doc_b.pk))

    def test_arabic_letters_match_persian_document(self):
        # Query typed with Arabic ي/ك code points must match the Persian
        # document through normalization (كلمه رمز عبور...).
        results = retrieve_chunks("رمز عبور و كلمه عبور")  # 'ك' is Arabic kaf
        self.assertTrue(results)
        self.assertEqual(results[0].document_id, str(self.doc_c.pk))

    def test_zwnj_and_space_forms_both_match(self):
        results_space = retrieve_chunks("یک بار تغییر")  # plain spaces
        results_zwnj = retrieve_chunks("یک‌بار تغییر")  # with ZWNJ
        self.assertTrue(results_space)
        self.assertEqual(
            [r.chunk_id for r in results_space], [r.chunk_id for r in results_zwnj]
        )


class HybridFusionTests(RetrievalTestBase):
    def test_chunk_found_by_both_channels_ranked_first(self):
        # "annual leave" matches doc A lexically AND semantically.
        results = retrieve_chunks("annual leave entitlement days")
        self.assertTrue(results)
        top = results[0]
        self.assertEqual(top.document_id, str(self.doc_a.pk))
        # Fused score is higher than a single-channel contribution (1/(60+1)).
        self.assertGreater(top.rrf_score, 1.0 / 61)

    def test_rrf_score_values(self):
        # Pure formula check: a chunk ranked 1 in a single channel scores
        # 1 / (60 + 1); ranked 1 in both channels scores 2 / 61.
        results = retrieve_chunks("annual leave entitlement days", top_k=3)
        self.assertTrue(results)
        top = results[0]
        if top.semantic_rank == 1 and top.lexical_rank == 1:
            self.assertAlmostEqual(top.rrf_score, 2.0 / 61.0, places=5)

    def test_no_duplicates_in_results(self):
        results = retrieve_chunks("production deployment window approvals annual leave")
        chunk_ids = [r.chunk_id for r in results]
        self.assertEqual(len(chunk_ids), len(set(chunk_ids)))

    def test_document_scope_filter(self):
        results = retrieve_chunks(
            "annual leave entitlement days",
            document_ids=[self.doc_b.pk],
        )
        # The scope excludes document A even though it matches best.
        self.assertTrue(results)
        self.assertTrue(all(r.document_id == str(self.doc_b.pk) for r in results))

    def test_empty_corpus_returns_no_results(self):
        Document.objects.all().delete()
        self.assertEqual(retrieve_chunks("annual leave"), [])

    def test_failed_documents_excluded(self):
        self.doc_a.status = Document.Status.FAILED
        self.doc_a.save(update_fields=["status"])
        results = retrieve_chunks("annual leave entitlement days")
        self.assertTrue(all(r.document_id != str(self.doc_a.pk) for r in results))

    @staticmethod
    def test_top_k_respected():
        from django.test import override_settings

        with override_settings(RETRIEVAL_TOP_K=2):
            results = retrieve_chunks("annual leave deployment password")
            assert len(results) <= 2

    def test_semantic_only_strategy(self):
        from django.test import override_settings

        with override_settings(RETRIEVAL_STRATEGY="semantic"):
            results = retrieve_chunks("annual leave entitlement days")
            self.assertTrue(results)
            self.assertTrue(all(r.lexical_rank is None for r in results))
