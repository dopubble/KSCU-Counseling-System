import base64
from pathlib import Path

# 1. 로컬 프로젝트에 이미 만들어두신 b64 파일의 경로 설정
SHIN_B64_PATH = Path("data/restore/shinyounghwa.b64")
HAN_B64_PATH = Path("data/restore/hanjini.b64")

# 2. 서버에서 누락된 정확한 절대 경로 매칭
files_to_restore = [
    {
        "name": "신영화 님 사례발표보고서",
        "b64_path": SHIN_B64_PATH,
        "target_path": "/data/media/presentation_board/1/posts/2026/06/한기상전문상담사_사례발표보고서이_옥.pdf"
    },
    {
        "name": "한진이 님 사례발표보고서",
        "b64_path": HAN_B64_PATH,
        "target_path": "/data/media/presentation_board/1/posts/2026/05/한기상전문상담사_사례발표보고서_홍o서님.pdf"
    }
]

print("🚀 PDF 파일 자동 복구 스크립트를 시작합니다...")

for file_info in files_to_restore:
    b64_file = file_info["b64_path"]
    target = Path(file_info["target_path"])
    
    # 서버 환경(Railway)과 로컬 환경 모두에서 작동할 수 있도록 체크
    # 배포 후 Railway 서버 안에서는 b64 파일이 루트 기준 아래 경로에 존재하게 됩니다.
    server_b64_path = Path("/app") / b64_file if not b64_file.exists() else b64_file

    if not server_b64_path.exists():
        print(f"❌ 오류: {b64_file.name} 파일을 찾을 수 없습니다. (경로 확인 필요)")
        continue
        
    try:
        # b64 파일 읽기
        b64_content = server_b64_path.read_bytes()
        
        # 상위 폴더 생성 (/data/media/...)
        target.parent.mkdir(parents=True, exist_ok=True)
        
        # 디코딩 후 PDF 저장
        pdf_bytes = base64.b64decode(b64_content)
        target.write_bytes(pdf_bytes)
        print(f"✅ {file_info['name']} 복구 완료!")
        print(f"   -> {target}")
        
    except Exception as e:
        print(f"❌ {file_info['name']} 복구 중 오류 발생: {e}")

print("✨ 모든 복구 작업이 끝났습니다.")