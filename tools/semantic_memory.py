from uuid import uuid4

import chromadb

from config import DATA_DIR


_chroma_client = chromadb.PersistentClient(path=str(DATA_DIR / "chroma_db"))
_collection = _chroma_client.get_or_create_collection(name="design_memories")


def save_memory(text: str) -> dict:
    memory_id = str(uuid4())
    _collection.add(documents=[text], ids=[memory_id])
    return {"saved": True, "message": "Design memory saved successfully.", "id": memory_id}


def search_memory(query: str) -> dict:
    results = _collection.query(
        query_texts=[query],
        n_results=3,
    )
    memories = results.get("documents", [[]])[0]
    formatted_memories = [
        f"{index}. {memory}" for index, memory in enumerate(memories, start=1)
    ]
    return {"memories": formatted_memories}
