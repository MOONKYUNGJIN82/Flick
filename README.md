# Flick

Windows EXR 및 이미지 시퀀스 플레이어. EXR, JPG, PNG, TGA와 Blender Cryptomatte를 지원합니다. 작업 구간을 RAM에 읽어 24/30/60fps로 재생하며 OCIO 표시 변환을 제공합니다.

설치 파일과 업데이트는 [Releases](https://github.com/MOONKYUNGJIN82/Flick/releases)에서 배포합니다. 사용법과 성능 측정은 [FLICK_README.md](FLICK_README.md)에 있습니다.

Windows에서 빌드하려면 Python 3.12와 Inno Setup 6이 필요합니다.

```powershell
python -m pip install -r requirements-flick.txt pyinstaller
python packaging/build_flick.py
```
