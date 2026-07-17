"""
state_migrate.py — trading_server.py의 "수집데이터 + 학습된 점수로직 변경사항"만
다른 기기로 옮기기 위한 export/import 도구. 잔고/포지션/서버-클라이언트 실시간
통신 파일(server_market.csv, server_account.csv 등)은 건드리지 않는다 —
그건 각 기기에서 새로 쌓이면 되는 값이라 옮길 대상이 아니라고 보고 뺐다.

옮기는 대상 (SCRIPT_DIR 안에 있으면 챙기고, 없으면 조용히 건너뜀):
  - learned_weights.json      : v8 자동학습이 계산한 현재 컴포넌트 배수(=점수로직 변경사항)
  - signal_outcomes.csv       : 신호별 결과 로그(수집결과) — 재학습의 원재료
  - weight_retrain_log.csv    : 재학습 이력(언제 왜 배수가 바뀌었는지 감사로그)
  - market_data_log_v3_*.csv  : data_collector.py가 쌓은 원본 시세/지표 수집데이터

사용법 (원본 기기에서):
    python3 state_migrate.py export                # SCRIPT_DIR에 state_export_<날짜>.zip 생성
    python3 state_migrate.py export /path/to/out.zip   # 저장 경로 직접 지정

사용법 (새 기기에서):
    python3 state_migrate.py import state_export_20260719.zip
    → 압축을 풀어서 이 기기의 SCRIPT_DIR(자동 판별)에 같은 파일명으로 복사한다.
    → 이미 같은 이름의 파일이 있으면(예: signal_outcomes.csv를 이미 쌓고 있었다면)
      기본은 덮어쓰지 않고 signal_outcomes.import_<타임스탬프>.csv 식으로 저장한다.
    → 그냥 덮어써도 되면 뒤에 --overwrite를 붙인다:
        python3 state_migrate.py import state_export_20260719.zip --overwrite
      (기존 signal_outcomes.csv/learned_weights.json 등을 새로 가져온 걸로 완전히 교체함 — 되돌릴 수 없음)
"""
import os
import sys
import glob
import zipfile
from datetime import datetime

# trading_server.py와 정확히 같은 SCRIPT_DIR 판별 로직 (안드로이드 공용저장소 우선,
# 안 되면 이 스크립트가 있는 폴더로 폴백)
try:
    _fallback_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _fallback_dir = os.getcwd()

_ANDROID_PUBLIC_DIR = "/storage/emulated/0/Documents"
try:
    os.makedirs(_ANDROID_PUBLIC_DIR, exist_ok=True)
    _test_path = os.path.join(_ANDROID_PUBLIC_DIR, ".write_test")
    with open(_test_path, "w") as _f:
        _f.write("ok")
    os.remove(_test_path)
    SCRIPT_DIR = _ANDROID_PUBLIC_DIR
except Exception:
    SCRIPT_DIR = _fallback_dir

print(f"데이터 폴더: {SCRIPT_DIR}")

FIXED_FILES = ["learned_weights.json", "signal_outcomes.csv", "weight_retrain_log.csv"]
GLOB_PATTERNS = ["market_data_log_v3_*.csv"]


def _collect_source_files():
    files = []
    for name in FIXED_FILES:
        p = os.path.join(SCRIPT_DIR, name)
        if os.path.exists(p):
            files.append(p)
    for pattern in GLOB_PATTERNS:
        files.extend(glob.glob(os.path.join(SCRIPT_DIR, pattern)))
    return sorted(set(files))


def do_export(out_path=None):
    files = _collect_source_files()
    if not files:
        print("옮길 파일이 하나도 없어 — learned_weights.json / signal_outcomes.csv / "
              "weight_retrain_log.csv / market_data_log_v3_*.csv 중 아무것도 안 보임.")
        return
    if out_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(SCRIPT_DIR, f"state_export_{ts}.zip")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, arcname=os.path.basename(f))
    print(f"내보내기 완료: {out_path}")
    print("포함된 파일:")
    for f in files:
        size_kb = os.path.getsize(f) / 1024
        print(f"  - {os.path.basename(f)} ({size_kb:.1f} KB)")
    print("\n이 zip 파일을 새 기기로 옮긴 뒤(USB/클라우드/메신저 등 아무 방법이나),")
    print("새 기기에서: python3 state_migrate.py import <옮긴zip경로>")


def do_import(zip_path, overwrite=False):
    if not os.path.exists(zip_path):
        print(f"파일을 못 찾음: {zip_path}")
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        print(f"압축 안에 {len(names)}개 파일: {names}")
        for name in names:
            dest = os.path.join(SCRIPT_DIR, name)
            if os.path.exists(dest) and not overwrite:
                # 기존 학습 데이터를 실수로 덮어쓰지 않게, 이미 있으면 별도 이름으로 저장
                base, ext = os.path.splitext(name)
                dest = os.path.join(SCRIPT_DIR, f"{base}.import_{ts}{ext}")
                print(f"  ⚠ {name} 이미 존재 — {os.path.basename(dest)} 로 저장(자동 병합 안 함, 수동 확인 필요)")
            elif os.path.exists(dest) and overwrite:
                print(f"  ⚠ {name} 이미 존재 — --overwrite 지정됨, 기존 파일을 덮어씀")
            with zf.open(name) as src, open(dest, "wb") as out:
                out.write(src.read())
            print(f"  ✅ {os.path.basename(dest)}")
    print(f"\n가져오기 완료: {SCRIPT_DIR}")
    if not overwrite:
        print("주의: signal_outcomes.csv/weight_retrain_log.csv가 .import_<시각> 이름으로 따로 저장됐다면,")
        print("기존 파일과 합쳐서 signal_outcomes.csv로 다시 저장해야 v8 재학습이 두 기기 데이터를 같이 본다")
        print("(pandas concat 등으로 직접 합치거나, 필요하면 합쳐주는 스크립트도 만들어줄게).")
        print("learned_weights.json은 병합이 의미없는 값(그 자체가 최종 배수)이라, 새로 온 파일을")
        print("그대로 쓰고 싶으면 learned_weights.json으로 이름을 바꿔주면 됨.")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("export", "import"):
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "export":
        do_export(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        if len(sys.argv) < 3:
            print("사용법: python3 state_migrate.py import <zip경로> [--overwrite]")
            sys.exit(1)
        overwrite_flag = "--overwrite" in sys.argv[3:]
        do_import(sys.argv[2], overwrite=overwrite_flag)
