"""Service layer for the questions app.

Modules
-------
``retrieval``  hybrid semantic+BM25 retrieval with Reciprocal Rank Fusion
``prompts``    grounded, injection-resistant prompt construction
``llm``        OpenRouter integration through LangChain (``ChatOpenAI``)
``rag``        end-to-end orchestration of a question
"""

from questions.services.rag import run_rag_pipeline
from questions.services.retrieval import retrieve_chunks

__all__ = ["run_rag_pipeline", "retrieve_chunks"]
