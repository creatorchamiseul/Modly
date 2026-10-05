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
- **게임 레디 파이프라인 내장** — 쿼드 리메쉬(+텍스처 자동 클린업)와 게임레디 마감(Finish) 노드로 게임 엔진에 넣을 수 있는 자산(100% 쿼드 · 진짜 쿼드 OBJ 동봉)까지 한 번에 만들 수 있습니다.

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

**노드 확장 (신규)**
- `mesh-remesher` — Remesh 노드: **Target Face Count**(기본 50,000 · SDF에서는 목표 쿼드 수, 0=자동) 옵션 + **Blender 복셀 기반 100% 쿼드 리메쉬**(SDF 모드) + 원본 결함 수선(구멍·바닥 시트) + **텍스처 자동 전이·클린업** (아래 "리메쉬 노드" 절 참고)
- `mesh-finisher` — **Finish (Game-Ready)** 노드: 리토폴로지 + Pixel Match + 디테일 베이크 + 텍스처 압축 (아래 "게임 레디 마감" 절 참고, [image-to-3dlab](https://github.com/Bingeljell/image-to-3dlab) 포팅)
- 신규 파일: `mesh-remesher/`에 `quad_remesh.py`·`sdf_remesh.py`·`blender_find.py`·`blender_texture_transfer.py` (+1,389줄) · `mesh-finisher/` 신규 9파일(`i3d_*.py` 7종 포함) · `samples/workflows/` 예제 3종 · `api/tests/test_mesh_remesher.py` 회귀 테스트

**안정성 수정**
- `api/routers/extensions.py`, `api/routers/generation.py` - 생성 작업이 진행 중일 때는 확장 리로드가 실행 중인 작업을 중단시키지 않도록 거부(409) 처리
- `api/requirements.txt` - MCP SDK(mcp)를 1.x로 상한 고정 (MCP 서버 스크립트 호환)
**설정/문서**
- `.gitignore` (+10/−5) — `data/`, `.cache/`, `.tools/`, `__pycache__/`, `*.pyc` 추가 + 중복된 launch 스크립트 ignore 항목 정리 (런타임 데이터·도구 커밋 방지)
- `package-lock.json` (±2) — 버전 필드 정합성 수정 (0.4.1 → 0.4.2)
- `README.md`, `USAGE.md` — 한국어 문서 + 원본 출처 크레딧

> 생성 AI 파이프라인·UI·워크플로우 시스템 등 **앱 본체 기능은 원본 그대로**이며, 위 노드 확장(리메쉬·게임레디 마감)과 안정성 수정만 추가했습니다.
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

---

## 게임 레디 마감 노드 (Finish — Mesh Finisher)

워크플로우에 **`Mesh Finisher` → `Finish (Game-Ready)`** 노드가 추가되었습니다. 생성된 하이폴리 자산을 게임에 넣을 수 있는 상태로 마감합니다. ([image-to-3dlab](https://github.com/Bingeljell/image-to-3dlab)의 Finish 체인을 Modly 노드로 포팅 — Apache-2.0, 제작자 Bingeljell, 자세한 내용은 노드 폴더의 파일 헤더 참고)

1. **리토폴로지** — 웰드 → 복셀 리메쉬 → QuadriFlow(거부되면 감면 폴백) → 스마트 UV → 원본 텍스처 전이 (Blender). 디시메이션 후에는 미세 파편(loose parts) 자동 제거 + 재웰드.
2. **Pixel Match** — Source Photo 슬롯에 사진을 연결하면 실루엣으로 카메라를 자동 정합해, 사진이 보는 표면에 **실제 픽셀**을 입힙니다. 글자·로고·얼굴이 "비슷하게 다시 그려진" 결과로 뭉개지지 않습니다. (numpy+Pillow, GPU 불필요)
3. **디테일 베이크** — 원본 하이폴리에서 노멀맵 + 메탈릭-러프니스 맵 전이 (Blender)
4. **텍스처 압축** — JPEG 재인코딩 (컬러 2048, 데이터맵 1024, 품질 90) — 실측: **292MB → 7.4MB**

**사용법**: 이미지→3D 생성 체인 뒤에 `Finish (Game-Ready)` 노드를 붙이고, `Source Photo` 슬롯에 같은 입력 이미지를 연결하세요. 파라미터: Target Face Count(기본 40,000) · Voxel Size(0.004) · Atlas Size(2048) · Pixel Match(auto/off) · Bake Surface Detail · Compress Textures. **샘플 03(게임레디 마감)**은 `samples/workflows/`에 동봉되어 있습니다 — Workflows 탭 → **Import**로 불러오세요.

**Blender 4.2+ 필요 (리토폴로지·베이크 단계)**: 자동으로 아래 순서로 찾습니다 — `MODLY_BLENDER` 환경변수 → PATH의 `blender` → 앱 폴더(상위 포함)의 `.tools\blender-*\blender.exe`(예: `.tools\blender-4.5.14-windows-x64\blender.exe`) → `C:\Program Files\Blender Foundation\`. Blender는 [blender.org](https://www.blender.org/download/)에서 무료로 받습니다 (자동 설치하지 않음 — 포터블 .zip을 `.tools`에 풀어두면 끝).

### 리메쉬 노드 (mesh-remesher) — 쿼드 리메쉬 + 텍스처 클린업
- **SDF(기본) 모드**: Blender 복셀 리메쉬로 **완전한 쿼드(100%)의 닫힌 스킨**을 만들고, 원본 텍스처를 새 UV로 **베이크 전이**합니다. (Blender 4.2+ 필요 — 없으면 내장 numpy 리메쉬로 폴백)
- **원본 결함 자동 수선**: AI 생성물에 흔한 **스위스치즈형 구멍을 봉합**하고 **바닥 판(베이스 시트, 실측 52,850면)을 제거**합니다 → 50,228 쿼드 · 열림 0 검증.
- **텍스처 클린업 (v12)**: 구멍 봉합면의 **UV를 복원**하고, 매끈한 프록시 표면에서 베이크해 **레이 미스(검은 점)를 제거**, 알베도 **3단 디스펙클**(소스 스펙클 정리 포함)로 점 노이즈를 제거합니다 → 다크 핀홀 3,490→0 / 609→0 검증.
- 결과물: `mesh-remesher-<ts>.glb` (파이프라인 표준) + `mesh-remesher-<ts>_quads.obj` (**진짜 쿼드 메쉬** — GLB는 포맷 규칙상 삼각형으로 저장됩니다)
- 형상 보존: **프리클린(웰드로 미세 균열 봉합)** → 복셀 리메쉬 → **Shrinkwrap(원본 표면 리프로젝션)** → 스무딩 프록시 디테일 패스 → 라이트 데노이즈 → 스무스 셰이딩. 파괴적 단계마다 **홀 가드**로 구멍을 재봉합해, 열린 파편 메쉬(경계 엣지 수십만 개)도 하나의 닫힌 표면으로 재구성됩니다.
- Triangle/Quad-Dominant 모드도 Blender가 있으면 텍스처 전이를 수행합니다.
- 활용 예: `samples/workflows/`의 **샘플 04**(이미지→3D→쿼드 리메쉬 50k·텍스처 베이크) · **샘플 05**(+ 게임레디 마감 풀체인) — Workflows 탭 → Import로 불러오기

---

## 업데이트 노트 (Fork 요약)

- **2026-10-06 · 텍스처 클린업 (v12)** — 봉합면 UV 복원 · 스무딩 프록시 베이크(레이 미스=검은 점 제거) · 알베도 3단 디스펙클. (검증: 다크 핀홀 3,490→0 / 609→0 · Khronos 오류 0)
- **2026-10-06 · 구멍·바닥 수선 (v10)** — 스위스치즈형 구멍 봉합 + 바닥 판(52,850면) 제거. (검증: 50,228 쿼드 · 열림 0)
- **2026-10-06 · 쿼드 리메쉬 재작성** — Blender 복셀 기반 100% 쿼드 + Shrinkwrap 디테일 복원 + 텍스처 베이크 전이 (numpy SDF는 폴백).
- **2026-10-01 · Finish 노드 추가** — mesh-finisher: 리토폴로지 · Pixel Match · 디테일 베이크 · 텍스처 압축 · 디시메이션 후 파편 제거·재웰드.
- **2026-10-01 · 홀-세이프 리메싱** — 파괴적 단계마다 홀 가드.
- **2026-09-30 · 안정화·포터블** — 생성 중 확장 리로드 거부(409) · MCP SDK 1.x 고정 · 데이터 격리(`MODLY_USER_DATA_DIR`) · `launch.bat` 단일 런처 · 한국어 문서.