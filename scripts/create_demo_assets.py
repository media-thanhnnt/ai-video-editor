"""Generate a fully local 10-scene Asset Manager acceptance fixture.

No external network or copyrighted audio. This creates synthetic tones, not narration.
"""
from __future__ import annotations
import argparse
import copy
import math
import struct
import wave
from pathlib import Path
from PIL import Image, ImageDraw
from ai_video_editor.project import ProjectManager

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--projects-dir', type=Path, default=ROOT / 'projects')
    parser.add_argument('--project-id', default='phase12_demo')
    args = parser.parse_args()
    pm = ProjectManager(args.projects_dir, ROOT / 'config/default_project.json')
    project = pm.create_project(args.project_id, 'Phase 1.2 Demo — 10 scenes')
    storyboard = copy.deepcopy(project.storyboard)
    for index in range(1, 11):
        sid = f'{index:03d}'
        imgpath = project.folder / 'assets/images' / f'scene_{sid}.png'
        image = Image.new('RGB', (960, 540), (20 + index * 14, 40, 100))
        draw = ImageDraw.Draw(image)
        draw.rectangle((80, 80, 880, 460), outline=(245, 245, 245), width=5)
        draw.ellipse((150 + index * 25, 200, 280 + index * 25, 330), fill=(250, 210, 70))
        image.save(imgpath)
        audio = project.folder / 'assets/audio' / f'scene_{sid}.wav'
        with wave.open(str(audio), 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(48000)
            frames = bytearray()
            for sample in range(24000):
                amplitude = int(5000 * math.sin(2 * math.pi * (240 + index * 25) * sample / 48000))
                frames.extend(struct.pack('<h', amplitude))
            wav.writeframes(frames)
        storyboard['scenes'].append({'scene_id': sid, 'order': index,
                                     'narration': f'Demo scene {index}',
                                     'image': f'assets/images/scene_{sid}.png',
                                     'audio': f'assets/audio/scene_{sid}.wav'})
    pm.save_project(project.project_id, storyboard=storyboard)
    print(f'DEMO CREATED: {project.folder} (10 image + 10 WAV)')


if __name__ == '__main__':
    main()
