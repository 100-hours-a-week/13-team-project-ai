#!/usr/bin/env python
"""
scripts/upload_meetup_requests.py
─────────────────────────────────
model_meta.pkl 에 저장된 학습 데이터를
Qdrant meetup_requests 컬렉션에 업로드.

학습 완료 후 최초 1회만 실행하면 됩니다.

사용법:
    python scripts/upload_meetup_requests.py \\
        --meta models/model_meta.pkl \\
        --url  http://localhost:6333 \\
        --collection meetup_requests \\
        --batch-size 256
"""
import argparse
import pickle
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np

# ── 인자 파싱 ─────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="model_meta → Qdrant 업로드")
parser.add_argument("--meta",       default="models/model_meta.pkl")
parser.add_argument("--url",        default="http://localhost:6333")
parser.add_argument("--api-key",    default=None)
parser.add_argument("--collection", default="meetup_requests")
parser.add_argument("--batch-size", type=int, default=256)
parser.add_argument("--recreate",   action="store_true",
                    help="컬렉션이 이미 있으면 삭제 후 재생성")
args = parser.parse_args()

# ── qdrant-client 확인 ────────────────────────────────────────────
try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance, VectorParams,
        PointStruct, PayloadSchemaType,
    )
except ImportError:
    print("❌ qdrant-client 미설치 → pip install qdrant-client")
    sys.exit(1)

# ── meta 로드 ─────────────────────────────────────────────────────
meta_path = Path(args.meta)
if not meta_path.exists():
    print(f"❌ meta 파일 없음: {meta_path}")
    sys.exit(1)

print(f"📂 meta 로드: {meta_path}")
with open(meta_path, "rb") as f:
    meta = pickle.load(f)

pref_norm_train: np.ndarray = meta["pref_norm_train"]   # (N, 18) float32
req_ids_train:   List       = meta["req_ids_train"]      # List[str|int], length N
tr_pairs_by_req: Dict       = meta["tr_pairs_by_req"]    # {req_id: {rid: label}}
head_arr_train:  List       = meta["head_arr_train"]     # List[int], length N

N = len(req_ids_train)
DIM = pref_norm_train.shape[1]   # 18
print(f"✅ 학습 요청 수: {N}  |  벡터 차원: {DIM}")

# ── Qdrant 연결 ───────────────────────────────────────────────────
client = QdrantClient(url=args.url, api_key=args.api_key, timeout=10.0)
print(f"✅ Qdrant 연결: {args.url}")

# ── 컬렉션 생성/재생성 ────────────────────────────────────────────
existing = [c.name for c in client.get_collections().collections]

if args.collection in existing:
    if args.recreate:
        client.delete_collection(args.collection)
        print(f"🗑️  기존 컬렉션 삭제: {args.collection}")
    else:
        print(f"ℹ️  컬렉션 '{args.collection}' 이미 존재 — 덮어쓰지 않고 업로드 시작")
        print("   (재생성하려면 --recreate 플래그 추가)")

if args.collection not in existing or args.recreate:
    client.create_collection(
        collection_name=args.collection,
        vectors_config=VectorParams(
            size=DIM,
            distance=Distance.COSINE,   # pref 벡터는 L2 정규화돼 있으므로 코사인
        ),
    )
    print(f"✅ 컬렉션 생성: {args.collection}  (dim={DIM}, distance=COSINE)")

# ── 배치 업로드 ───────────────────────────────────────────────────
print(f"\n📤 업로드 시작 (배치 크기={args.batch_size}) ...")

total_uploaded = 0
batch: List[PointStruct] = []

for i in range(N):
    req_id = req_ids_train[i]
    vector = pref_norm_train[i].tolist()
    hc     = int(head_arr_train[i])
    pairs  = {str(rid): int(lbl)
              for rid, lbl in tr_pairs_by_req.get(req_id, {}).items()}

    # Qdrant point ID는 정수 또는 UUID 문자열
    # req_id가 문자열이면 UUID로 변환
    import uuid
    point_id = int(req_id) if str(req_id).isdigit() else str(uuid.uuid5(uuid.NAMESPACE_DNS, str(req_id)))

    batch.append(PointStruct(
        id=point_id,
        vector=vector,
        payload={
            "request_id":        str(req_id),
            "headcount":         hc,
            "restaurant_pairs":  pairs,   # {"21471025": 1, "33215904": 0, ...}
        },
    ))

    if len(batch) >= args.batch_size:
        client.upsert(collection_name=args.collection, points=batch)
        total_uploaded += len(batch)
        print(f"  {total_uploaded}/{N} 업로드...")
        batch = []

# 나머지
if batch:
    client.upsert(collection_name=args.collection, points=batch)
    total_uploaded += len(batch)

# ── 결과 확인 ─────────────────────────────────────────────────────
info = client.get_collection(args.collection)
print(f"\n✅ 업로드 완료!")
print(f"   업로드 건수  : {total_uploaded}")
print(f"   컬렉션 벡터 수: {info.vectors_count}")
print(f"   컬렉션 이름  : {args.collection}")
