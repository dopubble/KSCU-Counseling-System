# 사례발표 PDF 복구 (Railway) — 작성자 재업로드 없이

게시글·다운로드 URL은 DB에 그대로 두고, **서버 디스크에만 PDF를 다시 넣습니다.**

## ⚠️ 먼저: Volume 설정

Volume 없이 복구하면 **다음 배포 때 또 사라집니다.**

1. Railway → Web 서비스 → **Volumes** → Add Volume  
2. Mount Path: `/data/media`  
3. Variables: `MEDIA_ROOT=/data/media`  
4. 재배포 후 Deploy Logs에 `legacy media storage=filesystem (volume)` 확인

---

## 1단계 — 누락 목록 확인 (Railway Shell)

```bash
python manage.py shell
```

```python
from django.conf import settings
from apps.counseling.models import CasePresentationPost

print("MEDIA_ROOT =", settings.MEDIA_ROOT)
for p in CasePresentationPost.objects.filter(author__name__in=("신영화", "한진이")).select_related("author"):
    ok = p.file and p.file.storage.exists(p.file.name)
    print("OK" if ok else "MISSING", p.author.name, p.pk, p.file.name, p.title)
```

`MISSING` 과 `file.name` 경로를 메모합니다.

(코드 배포 후에는 `python manage.py restore_presentation_post_file --list-missing` 도 사용 가능)

---

## 2단계 — PC PDF를 Railway 서버로 옮기기

Windows PowerShell (파일 경로 수정):

```powershell
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\경로\신영화.pdf"))
$b64 | Out-File -Encoding ascii -NoNewline "C:\경로\신영화.b64"
```

`신영화.b64` 내용을 Railway Shell에 붙여넣어 디코드:

```python
import base64
from pathlib import Path

B64 = """
여기에 .b64 파일 내용 붙여넣기
""".replace("\n", "").strip()

Path("/tmp/shinyounghwa.pdf").write_bytes(base64.b64decode(B64))
print("written", Path("/tmp/shinyounghwa.pdf").stat().st_size)
```

한진이 파일도 `/tmp/hanjini.pdf` 등으로 반복.

(10MB 가까우면 `.b64` 를 나눠 붙이거나, 임시 HTTPS URL에 올린 뒤 Shell에서 `curl -o /tmp/file.pdf URL`)

---

## 3단계 — 기존 DB 경로에 복구

### A) 관리 명령 (저장소에 명령이 배포된 경우)

```bash
python manage.py restore_presentation_post_file --author-name 신영화 --source /tmp/shinyounghwa.pdf
python manage.py restore_presentation_post_file --author-name 한진이 --source /tmp/hanjini.pdf
```

### B) 명령 없이 Shell만 (당장 배포 없이)

```python
import shutil
from pathlib import Path
from django.conf import settings
from apps.counseling.models import CasePresentationPost

def restore(author_name, source_path):
    post = CasePresentationPost.objects.select_related("author").get(author__name=author_name)
    dest = Path(settings.MEDIA_ROOT) / post.file.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, dest)
    ok = post.file.storage.exists(post.file.name)
    print(post.author.name, "exists=", ok, "size=", dest.stat().st_size, "path=", dest)

restore("신영화", "/tmp/shinyounghwa.pdf")
restore("한진이", "/tmp/hanjini.pdf")
```

작성자당 게시글이 여러 개면 `get(author__name=...)` 대신 `post_pk` 로 지정:

```python
post = CasePresentationPost.objects.get(pk="위 1단계에서 본 UUID")
```

---

## 4단계 — 웹에서 다운로드 테스트

동일 게시글 상세 → 암호 PDF 다운로드 → 파일 저장 확인.

---

## 직접 “폴더에 넣기”에 대해

- 운영 서버의 `media/` 는 **본인 PC 탐색기로 열 수 없습니다.**
- Railway Shell(또는 Volume 마운트된 컨테이너 안)에서  
  `MEDIA_ROOT` + DB의 `file.name` 경로에 파일을 두는 것이 “폴더에 넣기”와 같습니다.
- Django Admin / 상담사 화면에는 **대신 올리기** 기능이 없습니다.
