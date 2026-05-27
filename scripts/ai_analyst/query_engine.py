"""RAG query engine using Ollama and ChromaDB."""

import chromadb
import ollama
from typing import Optional

from .config import (
    CHROMA_DIR, COLLECTION_NAME, OLLAMA_MODEL, EMBED_MODEL,
    OLLAMA_BASE_URL, SYSTEM_PROMPT
)


class RAGEngine:
    """Retrieval-Augmented Generation engine for trading analysis."""

    def __init__(self):
        """Initialize Chroma client and collection."""
        self.chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        self.collection = self.chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}
        )

    def query(
        self,
        question: str,
        n_results: int = 8,
        where: Optional[dict] = None,
        stream: bool = True,
    ) -> str:
        """Execute a RAG query against the knowledge base.

        Args:
            question: Natural language question
            n_results: Number of documents to retrieve
            where: Optional Chroma where filter
            stream: Whether to stream response

        Returns:
            LLM response as string
        """

        # Embed the question
        try:
            embed_response = ollama.embeddings(
                model=EMBED_MODEL,
                prompt=question,
            )
            question_embedding = embed_response.get("embedding")
        except Exception as e:
            return f"Error embedding question: {e}"

        # Retrieve relevant documents
        try:
            results = self.collection.query(
                query_embeddings=[question_embedding],
                n_results=n_results,
                where=where,
            )
        except Exception as e:
            return f"Error querying collection: {e}"

        if not results or not results.get("documents") or not results["documents"][0]:
            return "No relevant documents found in the knowledge base."

        # Build context from retrieved documents
        documents = results["documents"][0]
        context = "\n---\n".join(documents)

        # Build prompt
        prompt = f"""{SYSTEM_PROMPT}

# Context from Knowledge Base:
{context}

# Question:
{question}

# Answer:"""

        # Query Ollama with streaming
        try:
            if stream:
                return self._stream_response(prompt)
            else:
                response = ollama.chat(
                    model=OLLAMA_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                )
                return response["message"]["content"]
        except Exception as e:
            return f"Error generating response: {e}"

    def _stream_response(self, prompt: str) -> str:
        """Stream a response from Ollama and print to terminal."""
        full_response = []

        try:
            stream = ollama.chat(
                model=OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
            )

            for chunk in stream:
                content = chunk.get("message", {}).get("content", "")
                if content:
                    print(content, end="", flush=True)
                    full_response.append(content)

            print()  # Final newline
            return "".join(full_response)

        except Exception as e:
            error_msg = f"\nError during streaming: {e}"
            print(error_msg)
            return error_msg

    def get_collection_stats(self) -> dict:
        """Get stats about the current collection."""
        count = self.collection.count()
        return {
            "total_documents": count,
            "collection_name": COLLECTION_NAME,
        }
