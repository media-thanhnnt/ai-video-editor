"""Phase 1.6: PCM placement, duration safety, AAC/MP4 validation."""
from __future__ import annotations

import math
import shutil
import struct
import tempfile
import unittest
import wave
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

from ai_video_editor.audio import AudioError, build_voice_mix, inspect_final, mux_project_audio
from ai_video_editor.timeline import validate_timeline


def tone(path:Path, frames:int, freq:int=440):
    path.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(path),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(48000)
        w.writeframes(b''.join(struct.pack('<h',round(5000*math.sin(2*math.pi*freq*i/48000))) for i in range(frames)))


def fixture(root:Path, wrong_duration:bool=False):
    audio=root/'assets'/'audio'/'scene_001.wav'
    tone(audio,4801 if wrong_duration else 4800)
    track={'scene_id':'001','source':'assets/audio/scene_001.wav',
           'start_sample':2400,'duration_samples':4800,'end_sample':7200,
           'start_seconds':.05,'end_seconds':.15}
    video={'scene_id':'001','image':'assets/images/scene_001.png',
           'start_frame':0,'end_frame':12,'duration_frames':12,
           'head_padding_frames':2,'tail_padding_frames':5,
           'transition_out':{'type':'none','duration_frames':0}}
    timeline={'time_base':{'fps':'30/1','audio_sample_rate':48000},
              'resolution':{'width':320,'height':180},
              'video_track':[video],'voice_track':[track],
              'total_frames':12,'total_samples':19200}
    project=SimpleNamespace(folder=root,config={'output':{'audio_channels':2}})
    return project,timeline


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_silence_and_voice_positions(self):
        project,timeline=fixture(self.root)
        dest=self.root/'work'/'audio'/'voice_mix.wav'
        report=build_voice_mix(project,timeline,dest)
        self.assertEqual(report['total_samples'],19200)
        with wave.open(str(dest),'rb') as wav:
            self.assertEqual(wav.getnframes(),19200)
            self.assertEqual((wav.getnchannels(),wav.getframerate()),(2,48000))
            head=wav.readframes(2400)
            active=wav.readframes(4800)
            tail=wav.readframes(12000)
        self.assertFalse(any(head))
        self.assertTrue(any(active))
        self.assertFalse(any(tail))
        for idx in range(0,len(active),4):
            self.assertEqual(active[idx:idx+2],active[idx+2:idx+4])

    def test_reject_duration_mismatch_instead_of_trimming(self):
        project,timeline=fixture(self.root,wrong_duration=True)
        with self.assertRaisesRegex(AudioError,'decoded 4801 samples'):
            build_voice_mix(project,timeline,self.root/'mix.wav')
        self.assertFalse((self.root/'mix.wav').exists())

    def test_reject_overlapping_voice(self):
        project,timeline=fixture(self.root)
        timeline['voice_track'][0]['start_sample']=-1
        with self.assertRaises(Exception):
            build_voice_mix(project,timeline,self.root/'mix.wav')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg missing')
    def test_mp3_decoding_and_sample_placement(self):
        from ai_video_editor.audio import _run
        project,timeline=fixture(self.root)
        source=self.root/'assets/audio/scene_001.wav'
        mp3=self.root/'assets/audio/scene_001.mp3'
        _run(['ffmpeg','-hide_banner','-nostdin','-loglevel','error','-y',
              '-i',str(source),'-c:a','libmp3lame','-q:a','4',str(mp3)])
        timeline['voice_track'][0]['source']='assets/audio/scene_001.mp3'
        report=build_voice_mix(project,timeline,self.root/'mix.wav')
        self.assertEqual(report['total_samples'],19200)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg missing')
    def test_mux_inspection_and_video_frame_count(self):
        from ai_video_editor.audio import _run
        project,timeline=fixture(self.root)
        mix=self.root/'mix.wav'
        build_voice_mix(project,timeline,mix)
        out=self.root/'final.mp4'
        _run(['ffmpeg','-hide_banner','-nostdin','-loglevel','error','-y',
              '-f','lavfi','-i','color=c=blue:s=320x180:r=30:d=0.4',
              '-i',str(mix),'-map','0:v:0','-map','1:a:0',
              '-frames:v','12','-c:v','libx264','-pix_fmt','yuv420p',
              '-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-x264-params','colorprim=bt709:transfer=bt709:colormatrix=bt709:range=tv',
              '-c:a','aac','-profile:a','aac_low','-ar','48000','-ac','2',str(out)])
        details=inspect_final(out,timeline,{'output':{'audio_channels':2}})
        self.assertEqual(details['video_frames'],12)
        self.assertEqual(details['channels'],2)
        self.assertLessEqual(abs(details['aac_sample_difference']),2048)
        timeline['total_frames']=13
        with self.assertRaises(AudioError):
            inspect_final(out,timeline,{'output':{'audio_channels':2}})


if __name__=='__main__': unittest.main()
