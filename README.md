# Modly (Portable Build)

이미지 한 장 또는 텍스트 프롬프트로 **내 PC의 GPU에서 3D 모델을 생성**하는 데스크톱 앱의 포터블(폴더 자립형) 빌드입니다.

## 원본 출처 (Original Project)

이 저장소는 아래 원본 프로젝트(MIT License)를 기반으로 한 **수정 배포본**이며, 모든 크레딧은 원본 제작자에게 있습니다.

> **Based on [Modly](https://github.com/lightningpixel/modly) by [Lightning Pixel](https://github.com/lightningpixel)**

---

## 이 빌드의 특징

- **폴더 하나로 완결** — 프로그램 실행 데이터(모델·캐시·설정)가 전부 압축을 푼 폴더 안(`data\`, `.cache\`)에 저장됩니다. C드라이브를 어지럽히지 않습니다. (Windows 전용)
- **런처 하나면 끝** — `launch.bat` 더블클릭 → 의존성 설치 · 파이썬 런타임 · 빌드 · Electron 복구까지 **전부 자동**으로 준비하고 실행합니다.
- **모델은 앱에서 자동 다운로드** — 원하는 모델만 골라 받으면 됩니다.

## 원본과의 차이 (vs. upstream)

비교 기준: [lightningpixel/modly](https://github.com/lightningpixel/modly) `main` (커밋 `1476fd0`). 아래 파일들을 수정/추가했습니다.

**앱 코드 (최소 수정)**
- `electron/main/index.ts` (+7) — `MODLY_USER_DATA_DIR` 환경변수가 있으면 앱 데이터 경로를 그 폴더로 고정하는 옵션 추가 (미설정 시 원본과 동일 동작)
- `electron/main/ipc-handlers.ts` (+5) — 첫 실행 설정의 기본 데이터 폴더를, 위 옵션이 켜져 있으면 "앱 폴더 안"으로 제안

**런처 (`launch.bat`)**
- 런타임 데이터·캐시를 전부 폴더 내부(`data\`, `.cache\`)로 고정하는 환경변수 설정 추가
- 번들 파이썬 자동 다운로드 단계 추가 (원본 런처에는 없어서 첫 설정이 실패할 수 있음)
- Electron 바이너리 자동 복구 단계 추가
- 실행 방식: `npm run preview`(매번 재빌드 포함) → **직접 실행**으로 변경 (빠르고, 콘솔 창이 바로 닫힘)

**리메쉬 기능 확장 (신규)**
- src/areas/workflows/nodes/mesh-remesher/processor.py, manifest.json — Remesh 노드에 Target Face Count(총 면 수) 옵션 추가: 0 = 기존 엣지 길이 방식, 값 입력 시 리메쉬 후 정확히 그 면 수로 정리

**안정성 수정**
- `api/routers/extensions.py`, `api/routers/generation.py` - 생성 작업이 진행 중일 때는 확장 리로드가 실행 중인 작업을 중단시키지 않도록 거부(409) 처리
- `api/requirements.txt` - MCP SDK(mcp)를 1.x로 상한 고정 (MCP 서버 스크립트 호환)
**설정/문서**
- `.gitignore` (+4) — `data/`, `.cache/` 추가 (런타임 데이터 커밋 방지)
- `package-lock.json` (±2) — 버전 필드 정합성 수정 (0.4.1 → 0.4.2)
- `README.md`, `USAGE.md` — 한국어 문서 + 원본 출처 크레딧

> 생성 AI 파이프라인·UI·워크플로우 등 **앱 본체 기능은 원본 그대로**입니다.
## 요구사항

| 항목 | 내용 |
|---|---|
| OS | Windows 10/11 (64-bit) |
| Node.js | **20 이상** (최신 LTS 권장) — https://nodejs.org |
| 인터넷 | 첫 실행 구성요소 다운로드 · 모델 다운로드에 필요 |
| GPU | NVIDIA 권장 (모델별 VRAM 약 6~16GB) |

## 빠른 시작

1. 이 저장소를 **Code → Download ZIP** 으로 받아 **원하는 폴더**에 압축 해제 (폴더명·위치 자유, 공백 가능)
2. 폴더 안의 **`launch.bat` 더블클릭**
3. 자동 설치가 끝나면 앱이 열립니다 (첫 실행은 몇 분 소요)
4. 앱에서 **Models 탭 → Install from GitHub** 으로 모델 설치 + 다운로드
5. **Generate 탭**에서 이미지/프롬프트로 3D 생성!

자세한 사용법은 **[USAGE.md](USAGE.md)** 를 참고하세요.

## 라이선스

- **MIT License** — [LICENSE](LICENSE) 참고 (원본 저작권 표시 유지)
- 포크/재배포 시 원본 크레딧(`Based on Modly by Lightning Pixel`)을 유지해야 합니다.