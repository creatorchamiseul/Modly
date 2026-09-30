# Modly 사용법 (Portable Build)

> 원본: [Modly](https://github.com/lightningpixel/modly) by Lightning Pixel (MIT License)
> 이 저장소는 원본 기반의 포터블 빌드입니다 — 모든 데이터가 압축 푼 폴더 안에서 동작합니다.

## 0. 한눈에 보기

압축 해제 → `launch.bat` 더블클릭 → 자동 설치 → 앱 실행 → 모델 설치 → 3D 생성

## 1. 준비물

- **Windows 10/11 (64-bit)**
- **Node.js 20 이상** — https://nodejs.org 에서 설치 (설치 옵션은 기본값 그대로: PATH 등록)
- **인터넷 연결** — 첫 실행 구성요소 및 모델 다운로드에 필요
- (권장) **NVIDIA GPU** — 모델에 따라 VRAM 약 6~16GB (예: RTX 3060 이상 권장)

## 2. 설치 & 첫 실행

1. 저장소를 **Code → Download ZIP** 으로 받아 **원하는 폴더**에 압축 해제
   - 폴더 이름/위치는 자유입니다 (예: `D:\내 폴더\Modly`)
2. 폴더 안의 **`launch.bat` 더블클릭**
3. 자동 진행 (콘솔 창): JS 의존성 설치 → 번들 파이썬 다운로드 → 앱 빌드 → 앱 실행
   - 처음에는 PC/인터넷에 따라 **몇 분** 걸릴 수 있습니다
4. 앱 첫 화면 "Choose a data folder" → **기본값(폴더 안 `data`) 그대로 Continue 권장**
   - 모델 용량이 크므로 여유 공간이 충분한 드라이브를 선택하세요
5. 자동 설정(1~3분)이 끝나면 메인 화면이 열립니다

> 다음부터는 `launch.bat` 더블클릭만 하면 바로 열립니다.

## 3. 모델 설치 (필수)

앱 왼쪽 **Models** 탭 → **Install from GitHub** 에 주소를 입력합니다.

| 모델 | 주소 | 특징 |
|---|---|---|
| Hunyuan3D 2 Mini Turbo | https://github.com/lightningpixel/modly-hunyuan3d-mini-turbo-extension | 빠름(초 단위), 저사양 |
| Hunyuan3D 2 Mini | https://github.com/lightningpixel/modly-hunyuan3d-mini-extension | 표준 |
| Hunyuan3D 2 Mini Fast | https://github.com/lightningpixel/modly-hunyuan3d-mini-fast-extension | 절충형 |
| TripoSG | https://github.com/lightningpixel/modly-triposg-extension | 고품질 (약 8GB VRAM) |
| Trellis.2 GGUF | https://github.com/lightningpixel/modly-trellis2-gguf-extension | 최고 품질 + 텍스처 |

설치 후 모델 카드에서 **Download** 클릭 (모델별 2~25GB, 이어받기 지원).

## 4. 3D 생성하기

### 워크플로우 방식 (권장)

1. **Workflows** 탭에서 새 워크플로우 생성
2. 노드 연결: `Image` → `Generate Mesh(모델)` → (선택) `Remesh` → (선택) `Optimize Mesh` → (선택) `Texture Mesh` → `Add to Scene`
3. **Generate** 탭에서 워크플로우 선택 → 이미지 업로드 → **Generate 3D Model**
4. 완성된 메시가 씬에 추가되고 `data\workspace` 폴더에 저장됩니다

**팁**

- 고해상도 메시에 텍스처를 입히는 작업은 매우 오래 걸립니다 → 텍스처 앞에 `Optimize Mesh`(20만 면 수준)를 넣으면 훨씬 빠릅니다
- `Remesh` 노드의 `Target Face Count`로 총 면 수를 직접 지정할 수 있습니다 (기본 50,000 · 0 = 리메쉬 자동)
- 첫 생성은 모델 로딩 때문에 느릴 수 있습니다 (이후 빨라짐)

### 텍스트 → 3D (커뮤니티 확장)

`Text to Image` 확장을 설치하면 프롬프트로 시작하는 체인이 가능합니다:

`Text → Generate Image → Generate Mesh → (Texture Mesh) → Add to Scene`

- https://github.com/ryanmb30-source/modly-text-to-image

## 5. 데이터 위치 & 초기화

- **모든 데이터는 압축 푼 폴더 안**: `data\` (models · workspace · extensions · logs)
- **초기화**: `data` 폴더를 지우고 다시 실행 → 처음부터 재설정 (모델 재다운로드 필요)
- **폴더 이동**: 설정 완료 후에는 폴더를 옮기지 마세요. 옮기려면 `data` 삭제 후 다시 실행하세요.

## 6. 문제 해결

| 증상 | 해결 |
|---|---|
| "Node.js not found" | Node.js 설치 후 다시 실행 |
| 실행이 안 됨 / 꼬였을 때 | `node_modules` 폴더 삭제 후 `launch.bat` 재실행 (자동 재설치) |
| Electron 관련 오류 | 런처가 자동 복구를 시도합니다. 계속 실패 시: `node node_modules\electron\install.js` 실행 |
| 모델 다운로드 실패 | Models 탭에서 다시 Download (이어받기) |
| 로그 확인 | `data\logs\modly.log`, `data\logs\runtime.log` |
| 완전 초기화 | `data` + `node_modules` 삭제 후 재실행 |

## 크레딧 / 라이선스

- **Based on [Modly](https://github.com/lightningpixel/modly) by [Lightning Pixel](https://github.com/lightningpixel)**
- MIT License ([LICENSE](LICENSE) 참고) — 이 빌드는 원본의 수정 배포본이며 원본 크레딧을 유지합니다.