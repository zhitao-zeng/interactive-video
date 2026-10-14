"""Prepare one common asset set before comparing providers."""
from pathlib import Path
from novel_manga.interactive.runtime import runner_for
from novel_manga.util import run, atomic_write_json

VOICE_TEXT = '我叫林野，专门寻找地图上消失的地方。先看清脚下，再核对方向。你们选，我去核对。'


def build_images(root: Path, novel: Path) -> None:
    runner_for(root, novel, 'h3').build_assets()


def build_voice(root: Path, novel: Path) -> None:
    from novel_manga.providers.base import ImageResult
    runner = runner_for(root, novel, 'h3')
    directory = novel/'series_assets/voices'
    directory.mkdir(parents=True, exist_ok=True)
    voice = directory/'林野.wav'
    if voice.is_file() and (directory/'voices.json').is_file():
        return
    video = directory/'linye-voice-source.mp4'
    prompt = (
        'A contemporary live-action casting voice recording. '
        '<Subject 1> is the adult Chinese man from <Picture 1>, with the same face, short black hair '
        'and dark olive outdoor jacket. He is alone against a neutral plain backdrop. '
        'Static medium close-up. A calm, warm medium-low adult male voice with a slight breathiness, '
        'clear Mandarin, measured conversational pace. No music and no other voices. '
        'He speaks naturally from the start: <d>[Chinese] '+VOICE_TEXT+'</d> '
        'Natural lip synchronization. Finish the utterance then remain quietly attentive.'
    )
    if not video.is_file():
        runner.context.provider.create_video(prompt, ImageResult(path=novel/'series_assets/characters/character_001/turnaround.jpeg'),video,14)
    run(['ffmpeg','-y','-v','error','-i',str(video),'-vn','-ar','16000','-ac','1','-c:a','pcm_s16le',str(voice)])
    # This sample defines the voice reference; the actual episode utterances pass the existing ASR gate.
    atomic_write_json(directory/'voices.json', {'林野': {'path':'series_assets/voices/林野.wav',
                       'source':str(video.relative_to(novel)), 'text':VOICE_TEXT, 'voice_id':'linye-v1',
                       'origin':'generated-reference', 'voice_quality':'not independently accepted'}})
