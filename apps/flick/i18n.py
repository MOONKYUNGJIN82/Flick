"""Small, explicit UI catalogue; technical color-space names stay unchanged."""

EN = {
    '업데이트 확인': 'Check updates',
    '업데이트 확인 중…': 'Checking updates…',
    '업데이트 저장소가 설정되지 않았습니다.': 'Update repository is not configured.',
    'Flick {version} 준비 완료 · 앱 종료 후 설치합니다.': 'Flick {version} is ready · installs when you exit.',
    '이미 최신 버전입니다.': 'Already up to date.',
    '업데이트 확인 실패: {error}': 'Update check failed: {error}',
    'Image sequence player': 'Image sequence player',
    '이미지 시퀀스 플레이어': 'Image sequence player',
    '시퀀스 열기': 'Open sequence', '노출': 'Exposure', 'sRGB 표시': 'sRGB display',
    '패스': 'Pass', '원본 영상': 'Beauty', 'Cryptomatte 항목': 'Cryptomatte item',
    '컬러 미리보기': 'Color preview',
    '재생 해상도': 'Playback resolution',
    '디스크 프록시': 'Disk proxies',
    ' · 프록시 재사용 {hits}': ' · proxy hits {hits}',
    'RAM에 맞춰 해상도를 낮췄습니다: 1/{scale}': 'Reduced resolution to fit RAM: 1/{scale}',
    '화면 맞춤': 'Fit', 'RAM 캐시': 'RAM cache', '설정 파일…': 'Config file…',
    '내장 ACES 1.3': 'Built-in ACES 1.3', '시작 프레임': 'Work start',
    '내장 ACES 1.3 CG 설정': 'Built-in ACES 1.3 CG config',
    '디코드': 'Decode',
    '끝 프레임': 'Work end', '현재 → 시작': 'Set start', '현재 → 끝': 'Set end',
    '전체': 'Full range', '구간 읽기': 'Load work area', '취소': 'Cancel',
    '반복': 'Loop', '자동 (파일 형식)': 'Auto (file type)',
    '모든 프레임': 'Every frame', '실시간 우선': 'Real-time priority',
    '▶ 재생': '▶ Play', 'Ⅱ 정지': 'Ⅱ Pause',
    '로딩 후 재생 예약': 'Play when loaded',
    '구간 읽기 대기': 'Work area not loaded', '메모리 계산 중…': 'Estimating memory…',
    '읽는 중 %v / %m': 'Loading %v / %m',
    '준비 완료 · %m frames': 'Ready · %m frames', '로딩 실패': 'Load failed',
    '대기': 'Idle', '버퍼링…': 'Buffering…',
    'OCIO 꺼짐 · 기본 sRGB / Linear 표시': 'OCIO off · basic sRGB / Linear display',
    'OCIO 오류 · 이전 변환 유지: {error}': 'OCIO error · previous transform retained: {error}',
    'GPU 오류: {error}': 'GPU error: {error}',
    '설정 파일을 열지 못했습니다: {error}': 'Could not open config: {error}',
    '구간 변경 · 구간 읽기 또는 재생을 누르세요.': 'Work area changed · load it or press Play.',
    '로딩 취소 · 재생하려면 구간을 다시 읽으세요.': 'Load cancelled · load the work area to play.',
    '시퀀스를 찾는 중…': 'Finding sequence…',
    'RAM 한도 변경 · 구간을 다시 읽으세요.': 'RAM limit changed · load the work area again.',
    '{count:,} frames · 누락 {missing:,}개 (건너뛰어 재생)': '{count:,} frames · {missing:,} missing (skipped)',
    'GPU OCIO · {source} → {display} / {view}': 'GPU OCIO · {source} → {display} / {view}',
    '프레임 정보 읽기 실패: ': 'Could not inspect frame: ',
    '지정한 구간에 프레임이 없습니다. 시작·끝 번호를 확인하세요.': 'No frames in this range. Check the start and end numbers.',
    '잘못된 로딩 구간입니다.': 'Invalid work area.',
    '한 프레임이 캐시 한도보다 큽니다. RAM 한도를 높이세요.': 'A frame exceeds the RAM limit. Increase it.',
    '실제 프레임 용량이 RAM 한도를 넘었습니다. 구간을 줄이세요.': 'Decoded frames exceeded the RAM limit. Shorten the work area.',
    'Space 재생/정지   ·   ← → 프레임 이동   ·   휠 확대   ·   드래그 이동   ·   F 화면 맞춤':
        'Space Play/Pause   ·   ← → Step   ·   Wheel Zoom   ·   Drag Pan   ·   F Fit',
    'Ctrl+O  열기     Space  재생': 'Ctrl+O  Open     Space  Play',
    '이미지 한 장을 열면 시퀀스를 함께 불러옵니다': 'Open one image to load its sequence',
    '시퀀스의 이미지 한 장 선택': 'Choose an image in the sequence',
    'OCIO 설정 열기': 'Open OCIO config',
    '작업 구간 손잡이를 드래그해 읽기·재생 범위를 설정하세요.':
        'Drag the work-area handles to set the load and playback range.',
    '표시할 RGB 또는 Y 채널이 없습니다.': 'No RGB or Y channel to display.',
    'EXR, JPG, PNG 또는 TGA 파일을 선택하세요.': 'Choose an EXR, JPG, PNG, or TGA file.',
    'Deep EXR은 아직 지원하지 않습니다.': 'Deep EXR is not supported yet.',
    '이 설정의 입력 색공간을 자동으로 판단할 수 없습니다. Input을 직접 선택하세요.':
        'Cannot determine this config’s input space. Select Input manually.',
    'OCIO 설정에 scene_linear 역할이 필요합니다.': 'The OCIO config needs a scene_linear role.',
    '동적 OCIO 파라미터가 있는 설정은 아직 지원하지 않습니다.':
        'Dynamic OCIO parameters are not supported yet.',
    'OCIO LUT 수가 GPU 텍스처 슬롯 한도를 넘습니다.': 'OCIO LUTs exceed the GPU texture slot limit.',
    '화면 제출 {rate:.1f} fps · skip {skipped} · RAM {used:.2f}/{limit} GiB · 구간 필요 {required:.2f} GiB{waiting}':
        'Displayed {rate:.1f} fps · skipped {skipped} · RAM {used:.2f}/{limit} GiB · work area {required:.2f} GiB{waiting}',
}


def translate(language, key, **values):
    template = EN.get(key, key) if language == 'en' else key
    return template.format(**values)


def translate_error(language, message):
    if language != 'en':
        return message
    if message in EN:
        return EN[message]
    if message.startswith('선택 구간은 '):
        return message.replace('선택 구간은 ', 'Work area needs ').replace(
            '가 필요합니다. RAM 한도를 높이거나 구간을 줄이세요.',
            '. Increase the RAM limit or shorten the work area.')
    if message.startswith('프레임 정보 읽기 실패: '):
        return message.replace('프레임 정보 읽기 실패: ', EN['프레임 정보 읽기 실패: '], 1)
    return message
