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