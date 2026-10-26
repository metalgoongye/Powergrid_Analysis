# -*- coding: utf-8 -*-
"""[3단계 GeoAI] 파인튜닝 모델 — 점-거리 매칭 평가 (약라벨 regime에 맞는 지표).

YOLO 내장 mAP50은 IoU 0.5를 요구하는데, 약라벨(OSM 점+가정 상자)과 georef
오차(수십 px) 때문에 모델이 대충 맞는 위치를 잡아도 0으로 나온다. 그래서
예측 박스 '중심'이 OSM 점 반경 R 안에 있으면 정답으로 치는 점-거리 매칭으로
재현율·정밀도를 다시 잰다(제로샷을 평가한 방식과 동일).

- 모델: out/internal/yolo_out/best.pt
- 평가 타일: out/internal/yolo/val/images (서천·보령·은평, 학습에 안 쓴 권역)
- 타일 픽셀 → EPSG:5179: 타일 매니페스트의 아핀 사용
- 매칭 반경: 철탑 50m, 변전소 150m (제로샷 대조와 동일)
- 문턱 스윕으로 정밀도/재현율 곡선.
출력: out/internal/s3_finetuned_detections.csv + stdout 요약.
"""
import csv
import math
import os
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
INT = os.path.join(REPO, "out", "internal")
VAL_IMG = os.path.join(INT, "yolo", "val", "images")
BEST = os.path.join(INT, "yolo_out", "best.pt")
MANIFEST = os.path.join(DATA_ROOT, "powergrid_spatial_data", "정사영상_전국",
                        "tiles_pilot", "tiles_manifest.csv")
CLS_NAME = {0: "tower", 1: "substation"}
RADIUS = {"tower": 50.0, "substation": 150.0}
SWEEP = [0.05, 0.10, 0.15, 0.25, 0.40]
CONF_MIN = 0.03   # 추론 자체는 낮게 뽑고 스윕에서 거른다


def main():
    # 타일 아핀 (basename → a,b,d,e,x0,y0)
    aff = {}
    for t in csv.DictReader(open(MANIFEST, encoding="utf-8")):
        aff[os.path.basename(t["file"])] = t
    # OSM 기준 (평가 권역만)
    with open(os.path.join(INT, "s3_osm_groundtruth.csv"), encoding="utf-8") as f:
        osm = [r for r in csv.DictReader([l for l in f if not l.startswith("#")])
               if r["sigungu"] in ("서천군", "보령시", "은평구")]
    osm_by_cls = defaultdict(list)
    for g in osm:
        osm_by_cls[g["class"]].append((float(g["x_5179"]), float(g["y_5179"])))

    from ultralytics import YOLO
    model = YOLO(BEST)
    imgs = [os.path.join(VAL_IMG, f) for f in os.listdir(VAL_IMG) if f.endswith(".jpg")]
    print(f"평가 타일 {len(imgs)}개 추론 시작 (CPU — 수분 소요)")

    dets = []  # (cls, score, x, y)
    for i in range(0, len(imgs), 64):
        batch = imgs[i:i + 64]
        for res in model.predict(batch, conf=CONF_MIN, imgsz=1024, verbose=False):
            base = os.path.basename(res.path)
            a = aff.get(base)
            if a is None:
                continue
            A, B, D, E = float(a["a"]), float(a["b"]), float(a["d"]), float(a["e"])
            X0, Y0 = float(a["x0"]), float(a["y0"])
            for box, sc, cl in zip(res.boxes.xywh.tolist(), res.boxes.conf.tolist(),
                                   res.boxes.cls.tolist()):
                cx, cy = box[0], box[1]
                x = X0 + A * cx + B * cy
                y = Y0 + D * cx + E * cy
                dets.append((CLS_NAME[int(cl)], float(sc), x, y))
        print(f"  {min(i+64,len(imgs))}/{len(imgs)}")

    with open(os.path.join(INT, "s3_finetuned_detections.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["cls", "score", "x_5179", "y_5179"])
        w.writerows(dets)

    print(f"\n총 탐지 {len(dets)}건")
    print(f"{'문턱':>5} {'클래스':<11} {'탐지':>6} {'매칭':>5} {'정밀도':>7} {'재현율':>7}")
    for th in SWEEP:
        for cls in ("tower", "substation"):
            D = sorted([d for d in dets if d[0] == cls and d[1] >= th],
                       key=lambda d: -d[1])
            G = osm_by_cls[cls]
            used = set()
            matched = 0
            for _, _, x, y in D:
                best_j, best_d = None, RADIUS[cls]
                for j, (gx, gy) in enumerate(G):
                    if j in used:
                        continue
                    dd = math.hypot(gx - x, gy - y)
                    if dd < best_d:
                        best_j, best_d = j, dd
                if best_j is not None:
                    used.add(best_j); matched += 1
            p = matched / len(D) if D else float("nan")
            r = len(used) / len(G) if G else float("nan")
            print(f"{th:>5.2f} {cls:<11} {len(D):>6} {len(used):>5} "
                  f"{100*p:>6.1f}% {100*r:>6.1f}%")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
