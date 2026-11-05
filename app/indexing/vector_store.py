"""Qdrant 로컬 모드(서버 없이 폴더에 저장) Dense 색인 생성·로드.

로컬 모드는 한 폴더를 한 프로세스만 열 수 있다. API 서버가 켜져 있는 동안
다른 스크립트가 같은 폴더를 열면 오류가 나므로 서버를 끄고 실행한다.
"""
from dataclasses import asdict

from qdrant_client import QdrantClient, models

from app.ingestion.chunker import index_text

COLLECTION = "basic_chunks"


def build_vector_index(chunks, embedder, path):
    """청크를 임베딩해 path에 새로 저장한다. 같은 이름의 기존 컬렉션은 지운다."""
    client = QdrantClient(path=str(path))
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)
    client.create_collection(COLLECTION, vectors_config=models.VectorParams(
        size=embedder.dim, distance=models.Distance.COSINE))
    vectors = embedder.embed_passages([index_text(c) for c in chunks])
    client.upload_points(COLLECTION, points=[
        models.PointStruct(id=i, vector=v.tolist(), payload=asdict(c))
        for i, (c, v) in enumerate(zip(chunks, vectors))
    ])
    return client


def open_vector_index(path):
    client = QdrantClient(path=str(path))
    if not client.collection_exists(COLLECTION):
        raise FileNotFoundError(f"{path}에 색인이 없습니다. python -m scripts.build_index 를 먼저 실행하세요.")
    return client
