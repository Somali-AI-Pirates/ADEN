"""Qdrant Cloud Dense 색인 생성·로드.

접속 정보는 환경변수로 받는다 (저장소 루트의 .env, Git 제외):
    QDRANT_URL      클러스터 주소 (https://<cluster-id>.<region>.cloud.qdrant.io:6333)
    QDRANT_API_KEY  클러스터 API 키

팀이 클러스터 하나를 함께 쓴다. 그래서 컬렉션 이름에 데이터·모델·청크 크기를 넣고,
이미 있는 컬렉션은 recreate=True일 때만 지운다.
"""
import os
from dataclasses import asdict

from qdrant_client import QdrantClient, models

from app.ingestion.chunker import index_text


def connect():
    url, key = os.environ.get("QDRANT_URL"), os.environ.get("QDRANT_API_KEY")
    if not url or not key:
        raise RuntimeError("QDRANT_URL, QDRANT_API_KEY 환경변수가 없습니다. 저장소 루트의 .env를 확인하세요.")
    return QdrantClient(url=url, api_key=key, timeout=60)


def build_vector_index(client, collection, chunks, embedder, recreate=False):
    """청크를 임베딩해 collection에 저장한다."""
    if client.collection_exists(collection):
        if not recreate:
            raise FileExistsError(
                f"'{collection}' 컬렉션이 이미 있습니다. 팀원이 쓰는 중일 수 있으니 확인한 뒤 --recreate 로 다시 만드세요.")
        client.delete_collection(collection)
    client.create_collection(collection, vectors_config=models.VectorParams(
        size=embedder.dim, distance=models.Distance.COSINE))
    vectors = embedder.embed_passages([index_text(c) for c in chunks])
    client.upload_points(collection, wait=True, points=[
        models.PointStruct(id=i, vector=v.tolist(), payload=asdict(c))
        for i, (c, v) in enumerate(zip(chunks, vectors))
    ])


def open_vector_index(collection):
    client = connect()
    if not client.collection_exists(collection):
        raise LookupError(f"'{collection}' 컬렉션이 없습니다. python -m scripts.build_index 를 먼저 실행하세요.")
    return client
