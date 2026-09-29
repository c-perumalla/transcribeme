from app import devices

DSHOW_NEW = r'''
[dshow @ 000001c4d7a0c540] "Integrated Camera" (video)
[dshow @ 000001c4d7a0c540]   Alternative name "@device_pnp_\\?\usb#vid_04f2"
[dshow @ 000001c4d7a0c540] "Microphone Array (Realtek(R) Audio)" (audio)
[dshow @ 000001c4d7a0c540]   Alternative name "@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}\wave_{1}"
[dshow @ 000001c4d7a0c540] "CABLE Output (VB-Audio Virtual Cable)" (audio)
[dshow @ 000001c4d7a0c540]   Alternative name "@device_cm_{33D9A762}\wave_{2}"
dummy: Immediate exit requested
'''

DSHOW_OLD = r'''
[dshow @ 0000024a] DirectShow video devices (some may be both video and audio devices)
[dshow @ 0000024a]  "Integrated Webcam"
[dshow @ 0000024a]     Alternative name "@device_pnp_\\?\usb"
[dshow @ 0000024a] DirectShow audio devices
[dshow @ 0000024a]  "Microphone (USB Audio Device)"
[dshow @ 0000024a]     Alternative name "@device_cm_{33D9A762}\wave_{A}"
[dshow @ 0000024a]  "Stereo Mix (Realtek High Definition Audio)"
[dshow @ 0000024a]     Alternative name "@device_cm_{33D9A762}\wave_{B}"
dummy: Immediate exit requested
'''

AVFOUNDATION = '''
[AVFoundation indev @ 0x1] AVFoundation video devices:
[AVFoundation indev @ 0x1] [0] FaceTime HD Camera
[AVFoundation indev @ 0x1] AVFoundation audio devices:
[AVFoundation indev @ 0x1] [0] MacBook Pro Microphone
[AVFoundation indev @ 0x1] [1] Meeting Aggregate Device
: Input/output error
'''


def names(devs):
    return [d["name"] for d in devs]


def test_dshow_inline_format():
    assert names(devices.parse_dshow(DSHOW_NEW)) == [
        "Microphone Array (Realtek(R) Audio)",
        "CABLE Output (VB-Audio Virtual Cable)",
    ]


def test_dshow_header_format():
    assert names(devices.parse_dshow(DSHOW_OLD)) == [
        "Microphone (USB Audio Device)",
        "Stereo Mix (Realtek High Definition Audio)",
    ]


def test_avfoundation():
    devs = devices.parse_avfoundation(AVFOUNDATION)
    assert names(devs) == ["MacBook Pro Microphone", "Meeting Aggregate Device"]
    assert [d["is_aggregate"] for d in devs] == [False, True]
    assert devs[1]["index"] == 1
