import base64
import shutil
from pathlib import Path

# 1. 원래 들어가야 할 정확한 07월 최종 목적지 경로 설정
SHIN_TARGET = Path("/data/media/presentation_board/1/posts/2026/07/06/한기상전문상담사_사례발표보고서이_옥.pdf")
HAN_TARGET = Path("/data/media/presentation_board/1/posts/2026/07/05/한기상전문상담사_사례발표보고서_홍o서님.pdf")

print("🚀 [통합/교정] PDF 파일 자동 복구 스크립트를 시작합니다...")

# --- [신영화 님 파일 복구 및 07월 경로 이전] ---
SHIN_B64_PATH = Path("data/restore/shinyounghwa.b64")
shin_b64 = Path("/app") / SHIN_B64_PATH if not SHIN_B64_PATH.exists() else SHIN_B64_PATH

if shin_b64.exists():
    try:
        SHIN_TARGET.parent.mkdir(parents=True, exist_ok=True)
        pdf_bytes = base64.b64decode(shin_b64.read_bytes().strip())
        SHIN_TARGET.write_bytes(pdf_bytes)
        print(f"✅ 신영화 님 사례발표보고서 최종 교정 완료!\n   -> {SHIN_TARGET}")
    except Exception as e:
        print(f"❌ 신영화 님 복구 중 오류 발생: {e}")
else:
    print("❌ 신영화 님 b64 파일을 찾을 수 없습니다.")

# --- [한진이 님 파일 형식 자동 판별 및 복구] ---
restore_dir = Path("data/restore")
if not restore_dir.exists():
    restore_dir = Path("/app/data/restore")

# '한국기독교'로 시작하는 모든 복구 파일 리스트업
han_files = list(restore_dir.glob("한국기독교*"))

if not han_files:
    print("❌ 한진이 님 복구용 파일(한국기독교...)을 찾을 수 없습니다.")
else:
    HAN_TARGET.parent.mkdir(parents=True, exist_ok=True)
    success = False
    
    for f in han_files:
        try:
            file_bytes = f.read_bytes()
            
            # 케이스 B: 파일 내부가 진짜 %PDF 시그니처로 시작하는 바이너리인 경우
            if file_bytes.startswith(b"%PDF"):
                shutil.copy(f, HAN_TARGET)
                print(f"✅ 한진이 님 사례발표보고서 복구 완료! (원본 PDF 직접 복사)\n   -> {HAN_TARGET}")
                success = True
                break
                
            # 케이스 A: 파일 내부가 Base64 텍스트 문자열인 경우 디코딩 시도
            else:
                try:
                    decoded_bytes = base64.b64decode(file_bytes.strip())
                    if decoded_bytes.startswith(b"%PDF"):
                        HAN_TARGET.write_bytes(decoded_bytes)
                        print(f"✅ 한진이 님 사례발표보고서 복구 완료! (텍스트 디코딩 변환)\n   -> {HAN_TARGET}")
                        success = True
                        break
                except:
                    continue
        except Exception as e:
            print(f"⚠️ {f.name} 파일 처리 중 에러 발생: {e}")
            
    if not success:
        print("❌ 한진이 님 파일 중 유효한 PDF 데이터 형식을 찾지 못했습니다.")

print("✨ 모든 복구 및 경로 교정 작업이 마무리되었습니다.")