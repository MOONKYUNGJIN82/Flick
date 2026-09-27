"""OCIO processors are compiled on setting changes, never per frame."""
import PyOpenColorIO as ocio

BUILTIN = 'cg-config-v2.2.0_aces-v1.3_ocio-v2.4'


def load_config(path=None):
    config = ocio.Config.CreateFromFile(path) if path else ocio.Config.CreateFromBuiltinConfig(BUILTIN)
    config.validate()
    return config


def automatic_input(config, linear):
    candidates = (['Linear Rec.709 (sRGB)', 'lin_srgb', 'scene_linear'] if linear else
                  ['sRGB Encoded Rec.709 (sRGB)', 'sRGB', 'srgb_texture'])
    for name in candidates:
        space = config.getColorSpace(name)
        if space:
            return space.getName()
    raise ValueError('이 설정의 입력 색공간을 자동으로 판단할 수 없습니다. Input을 직접 선택하세요.')


def make_descriptors(config, source, display, view):
    scene = config.getColorSpace(ocio.ROLE_SCENE_LINEAR)
    if not scene:
        raise ValueError('OCIO 설정에 scene_linear 역할이 필요합니다.')
    transforms = [ocio.ColorSpaceTransform(src=source, dst=scene.getName()),
                  ocio.DisplayViewTransform(src=scene.getName(), display=display, view=view)]
    descriptions = []
    for transform, name, prefix in zip(transforms, ['flickInput', 'flickDisplay'], ['flick_in_', 'flick_out_']):
        desc = ocio.GpuShaderDesc.CreateShaderDesc()
        desc.setLanguage(ocio.GPU_LANGUAGE_GLSL_1_3)
        desc.setFunctionName(name)
        desc.setResourcePrefix(prefix)
        config.getProcessor(transform).getDefaultGPUProcessor().extractGpuShaderInfo(desc)
        if list(desc.getUniforms()):
            raise ValueError('동적 OCIO 파라미터가 있는 설정은 아직 지원하지 않습니다.')
        descriptions.append(desc)
    return descriptions
