# -*- coding: utf-8 -*-
"""[3단계 GeoAI] YOLO 학습 패키지 조립 + HF 업로드.

s3_make_yolo_labels.py가 만든 라벨·이미지목록을 받아, 실제 타일 이미지를
train/val images 폴더로 복사하고 data.yaml을 완성한 뒤 단일 zip으로 묶어
HF 비공개 데이터셋에 올린다. Kaggle 학습 노트북은 이 zip 하나만 받으면 된다.
(전 장면 zip 9GB를 Kaggle에서 다시 받지 않으려는 설계.)
"""
import os
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s3_hf_transfer import load_env

sys.stdout.reconfigure(encoding="utf-8")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
TILES = os.path.join(DATA_ROOT, "powergrid_spatial_data", "정사영상_전국", "tiles_pilot")
YOLO = os.path.join(REPO, "out", "internal", "yolo")
ZIP = os.path.join(DATA_ROOT, "powergrid_spatial_data", "정사영상_전국", "yolo_dataset.zip")


def main():
    for sp in ("train", "val"):
        imgdir = os.path.join(YOLO, sp, "images")
        os.makedirs(imgdir, exist_ok=True)
        lst = os.path.join(YOLO, f"images_list_{sp}.txt")
        n = 0
        for rel in open(lst, encoding="utf-8").read().split():
            src = os.path.join(TILES, rel)
            dst = os.path.join(imgdir, os.path.basename(rel))
            if os.path.isfile(src) and not os.path.isfile(dst):
                shutil.copy(src, dst)
            n += 1
        print(f"[{sp}] 이미지 {n}개 복사")

    with open(os.path.join(YOLO, "data.yaml"), "w", encoding="utf-8") as y:
        y.write("path: .\ntrain: train/images\nval: val/images\n"
                "names:\n  0: tower\n  1: substation\n")

    # zip (labels + images + data.yaml)
    if os.path.isfile(ZIP):
        os.remove(ZIP)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_STORED) as zf:
        for root, _, files in os.walk(YOLO):
            for fn in files:
                full = os.path.join(root, fn)
                zf.write(full, os.path.relpath(full, YOLO))
    print(f"zip {os.path.getsize(ZIP)/2**30:.2f} GB → {ZIP}")

    load_env()
    from huggingface_hub import HfApi
    api = HfApi(token=os.environ["HF_TOKEN"])
    repo = os.environ.get("HF_TILES_REPO") or f"{api.whoami()['name']}/wp2026-tiles-pilot"
    api.upload_file(path_or_fileobj=ZIP, path_in_repo="yolo_dataset.zip",
                    repo_id=repo, repo_type="dataset", token=os.environ["HF_TOKEN"])
    print(f"HF 업로드 완료: {repo}/yolo_dataset.zip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
