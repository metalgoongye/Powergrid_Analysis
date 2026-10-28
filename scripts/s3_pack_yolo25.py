# -*- coding: utf-8 -*-
"""[3단계 GeoAI] 항공 25cm YOLO 패키지 zip + HF 업로드.

s3_make_yolo25.py 산출(Z:\\2026_WP_out\\internal\\yolo25: images+labels+
dataset.yaml)을 단일 zip으로 묶어 HF 비공개 데이터셋에 올린다. Kaggle
노트북은 이 zip 하나(yolo25_dataset.zip)만 받는다. 영상 크롭은 재배포
금지 — private 강제. 대용량 산출물은 NAS(Z:) [사용자 결정 2026-10-28].
"""
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s3_hf_transfer import load_env

sys.stdout.reconfigure(encoding="utf-8")
YOLO = r"Z:\2026_WP_out\internal\yolo25"
ZIP = r"Z:\2026_WP_out\internal\yolo25_dataset.zip"


def main():
    if not os.path.isdir(YOLO):
        print(f"입력 없음: {YOLO} — 먼저 s3_make_yolo25.py 실행")
        return 1
    if os.path.isfile(ZIP):
        os.remove(ZIP)
    n = 0
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_STORED) as zf:
        for root, _d, files in os.walk(YOLO):
            for fn in files:
                full = os.path.join(root, fn)
                zf.write(full, os.path.relpath(full, YOLO))
                n += 1
    print(f"zip {n}파일 {os.path.getsize(ZIP)/2**30:.2f} GB → {ZIP}")

    load_env()
    token = os.environ.get("HF_TOKEN")
    if not token:
        print("HF_TOKEN이 .env에 없음 — 업로드 생략")
        return 1
    from huggingface_hub import HfApi
    api = HfApi(token=token)
    repo_id = os.environ.get("HF_TILES_REPO") or f"{api.whoami()['name']}/wp2026-tiles-pilot"
    api.create_repo(repo_id, repo_type="dataset", private=True, exist_ok=True)
    assert api.repo_info(repo_id, repo_type="dataset").private, \
        f"{repo_id} 가 비공개가 아님 — 중단 (영상 재배포 금지)"
    api.upload_file(path_or_fileobj=ZIP, path_in_repo="yolo25_dataset.zip",
                    repo_id=repo_id, repo_type="dataset", token=token)
    print(f"HF 업로드 완료: {repo_id}/yolo25_dataset.zip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
