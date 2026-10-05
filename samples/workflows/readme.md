# 샘플 워크플로우

Modly의 **Workflows 탭 → Import** 로 아래 파일을 불러오면 바로 사용할 수 있습니다.

| 파일 | 체인 | 필요한 것 |
|---|---|---|
| `sample-03-game-ready.json` | 이미지 → 3D → **게임레디 마감**(리토폴로지 + Pixel Match) | 생성 모델(예: Trellis2) · Blender 4.2+ |
| `sample-04-quad-remesh.json` | 이미지 → 3D → **쿼드 리메쉬**(SDF Quad 50k · 텍스처 베이크) | 생성 모델 · Blender 4.2+ |
| `sample-05-quad-remesh-game-ready.json` | 이미지 → 3D → 쿼드 리메쉬 → 게임레디 마감 (풀체인) | 생성 모델 · Blender 4.2+ |

- `mesh-remesher`(쿼드 리메쉬) · `mesh-finisher`(게임레디 마감) 노드는 앱에 내장되어 있습니다.
- Blender는 [blender.org](https://www.blender.org/download/)의 포터블 .zip을 앱 폴더에 풀어두면 자동으로 찾습니다 (4.2+).
- 이미지 노드에는 원하는 이미지를 직접 넣어주세요 — 샘플에는 이미지가 포함되지 않습니다.
