"""Join completed A/B samples without changing their speech or playback speed."""
from pathlib import Path
import json
import subprocess

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/romance-sd25-continuity-ab'
DELIVERY=ROOT/'delivery/romance/sd25-continuity-ab'


def run(args):
    subprocess.run(args,check=True,capture_output=True)


def metadata(path):
    return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]))


def main():
    results=json.loads((OUT/'results.json').read_text())
    if len(results)!=4 or any(r['status']!='generated' for r in results):
        raise ValueError('All four scheduled native clips must finish before assembly')
    DELIVERY.mkdir(parents=True,exist_ok=True)
    (DELIVERY/'owner').write_text('Codex / authorized SD2.5 26s A/B comparison / 2026-10-13\n')
    font=subprocess.check_output(['fc-match','-f','%{file}','Noto Sans CJK SC'],text=True).strip()
    record={'arms':{},'format':'1920x1080,25fps,H264/AAC','native_speech_and_speed_preserved':True}
    for arm,label in [('A','A  原提示词'),('B','B  房间状态＋Blender空间参考')]:
        clips=[OUT/(arm+'-'+cid)/'clip.mp4' for cid in ['clip_05','clip_06']]
        label_file=DELIVERY/(arm+'-label.txt');label_file.write_text(label)
        filters=[]
        for i in range(2):
            filters.append(f'[{i}:v]scale=1920:1080,fps=25,setsar=1,setpts=PTS-STARTPTS[v{i}]')
            filters.append(f'[{i}:a]aresample=48000,asetpts=PTS-STARTPTS[a{i}]')
        filters.append('[v0][a0][v1][a1]concat=n=2:v=1:a=1[body][voice]')
        filters.append(f"[body]drawtext=fontfile='{font}':textfile='{label_file}':fontsize=40:fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=12:x=40:y=30[picture]")
        target=DELIVERY/(arm+'.mp4')
        run(['ffmpeg','-y','-v','error','-i',str(clips[0]),'-i',str(clips[1]),'-filter_complex',';'.join(filters),
             '-map','[picture]','-map','[voice]','-c:v','libx264','-preset','fast','-crf','19','-pix_fmt','yuv420p',
             '-threads','8','-c:a','aac','-b:a','192k','-movflags','+faststart',str(target)])
        run(['ffmpeg','-v','error','-i',str(target),'-f','null','-'])
        info=metadata(target)
        record['arms'][arm]={'video':str(target),'duration':float(info['format']['duration']),
            'native_clips':[str(p) for p in clips],'streams':info['streams']}
    target=DELIVERY/'comparison.mp4'
    run(['ffmpeg','-y','-v','error','-i',str(DELIVERY/'A.mp4'),'-i',str(DELIVERY/'B.mp4'),
        '-filter_complex','[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]',
        '-map','[v]','-map','[a]','-c:v','libx264','-preset','fast','-crf','19','-pix_fmt','yuv420p',
        '-threads','8','-c:a','aac','-b:a','192k','-movflags','+faststart',str(target)])
    run(['ffmpeg','-v','error','-i',str(target),'-f','null','-'])
    record['comparison']={'video':str(target),'duration':float(metadata(target)['format']['duration']),
        'order':'A then B, each approximately 26 seconds; soundtracks are not mixed'}
    (DELIVERY/'delivery.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:round(v['duration'],3) for k,v in record['arms'].items()}),flush=True)


if __name__=='__main__':main()
