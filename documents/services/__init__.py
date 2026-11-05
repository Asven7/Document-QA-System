"""Service layer for the documents app.

Modules
-------
``extraction``        DOCX -> plain text (python-docx)
``chunking``          text -> chunks (LangChain RecursiveCharacterTextSplitter)
``embeddings``        chunks -> vectors (fastembed, LangChain ``Embeddings``)
``vector_store``      persistent vector index (ChromaDB + LangChain)
``document_service``  orchestration of the full document lifecycle
"""

from documents.services.chunking import split_document_text
from documents.services.document_service import (
    create_document,
    delete_document,
    process_document,
    reprocess_document,
    update_document,
)
from documents.services.extraction import DocxExtractionError, extract_docx_text

__all__ = [
    "split_document_text",
    "create_document",
    "delete_document",
    "process_document",
    "reprocess_document",
    "update_document",
    "extract_docx_text",
    "DocxExtractionError",
]
