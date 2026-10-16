# -*- coding: utf-8 -*-
"""[3단계 GeoAI] HuggingFace 비공개 데이터셋 경유 타일·결과 전송.

기관망에서 Google Drive가 차단되어, Colab과의 파일 교환을 HF 비공개
데이터셋으로 한다 (huggingface.co는 접속 가능 확인 2026-10-16).

- upload  : tiles_pilot의 장면 폴더를 장면별 zip으로 묶어(스테이징 폴더)
            비공개 dataset 저장소에 매니페스트와 함께 올린다.
            영상 타일은 라이선스상 재배포 금지 대상이므로 반드시 private.
- download: Colab이 되올린 out/detections_*.csv 를 out/internal/로 내려받는다.

토큰은 .env의 HF_TOKEN (write 권한). 저장소 ID는 HF_TILES_REPO
(기본: <계정>/wp2026-tiles-pilot).

사용: python scripts/s3_hf_transfer.py upload [장면ID ...]
      python scripts/s3_hf_transfer.py download
"""
import os
import sys
import zipfile

sys.stdout.reconfigure(encoding="utf-8")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
TILES = os.path.join(DATA_ROOT, "powergrid_spatial_data", "정사영상_전국", "tiles_pilot")
STAGE = os.path.join(DATA_ROOT, "powergrid_spatial_data", "정사영상_전국", "tiles_pilot_zips")
OUT_INTERNAL = os.path.join(REPO, "out", "internal")


def load_env():
    p = os.path.join(REPO, ".env")
    if os.path.isfile(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("upload", "download"):
        print(__doc__)
        return 1
    load_env()
    token = os.environ.get("HF_TOKEN")
    if not token:
        print("HF_TOKEN이 .env에 없음 — huggingface.co에서 write 토큰 발급 후"
              " .env에 HF_TOKEN=hf_... 추가")
        return 1
    from huggingface_hub import HfApi
    api = HfApi(token=token)
    repo_id = os.environ.get("HF_TILES_REPO") or f"{api.whoami()['name']}/wp2026-tiles-pilot"

    if sys.argv[1] == "upload":
        api.create_repo(repo_id, repo_type="dataset", private=True, exist_ok=True)
        info = api.repo_info(repo_id, repo_type="dataset")
        assert info.private, f"{repo_id} 가 비공개가 아님 — 중단 (영상 재배포 금지)"
        only = set(sys.argv[2:])
        scenes = [d for d in sorted(os.listdir(TILES))
                  if os.path.isdir(os.path.join(TILES, d)) and (not only or d in only)]
        os.makedirs(STAGE, exist_ok=True)
        api.upload_file(path_or_fileobj=os.path.join(TILES, "tiles_manifest.csv"),
                        path_in_repo="tiles_manifest.csv",
                        repo_id=repo_id, repo_type="dataset")
        for sid in scenes:
            z = os.path.join(STAGE, f"{sid}.zip")
            if not os.path.isfile(z):
                with zipfile.ZipFile(z, "w", zipfile.ZIP_STORED) as zf:  # jpg라 재압축 불필요
                    for fn in sorted(os.listdir(os.path.join(TILES, sid))):
                        zf.write(os.path.join(TILES, sid, fn), f"{sid}/{fn}")
            print(f"업로드 중: {sid}.zip ({os.path.getsize(z)/2**20:.0f} MB)")
            api.upload_file(path_or_fileobj=z, path_in_repo=f"{sid}.zip",
                            repo_id=repo_id, repo_type="dataset")
        print(f"완료 — Colab 셀 2에 HF_REPO='{repo_id}' 설정")
    else:
        from huggingface_hub import hf_hub_download
        os.makedirs(OUT_INTERNAL, exist_ok=True)
        for name in ("detections_raw.csv", "detections_dedup.csv"):
            p = hf_hub_download(repo_id=repo_id, repo_type="dataset", token=token,
                                filename=f"out/{name}")
            dst = os.path.join(OUT_INTERNAL, name)
            with open(p, "rb") as fi, open(dst, "wb") as fo:
                fo.write(fi.read())
            print(f"수신: {dst} ({os.path.getsize(dst)/2**10:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
