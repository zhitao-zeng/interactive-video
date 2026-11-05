"""Executed by SoulTuner's existing Python environment, not the studio environment."""
import asyncio
import json
import os
from pathlib import Path
import sys


async def run(request):
    config=request['runtime']
    os.environ.update(CUDA_VISIBLE_DEVICES='',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
        NEO4J_URI=config['neo4j_uri'],NEO4J_USER='neo4j',NEO4J_PASSWORD='unused-loopback',
        MUSIC_MOCK_MODE='0',MUSIC_WEB_SEARCH_ENABLED='0',EVAL_DISABLE_SIDE_EFFECTS='true',
        TOOL_PLAN_EXECUTION_ENABLED='true',EXPLANATION_MODE='tuner_async',DENSE_TEXT_AUDIO_BACKEND='m2d',
        GOOGLE_BERT_BERT_BASE_UNCASED_PATH=config['bert_path'],
        M2D_CLAP_CHECKPOINT=config['m2d_checkpoint'],M2D_CLAP_WEIGHT_DIR=str(Path(config['m2d_checkpoint']).parent),
        MUSIC_API_BASE_URL=config.get('audio_base_url','http://127.0.0.1:18501'))
    key=os.environ['ST_LLM_KEY'];base=os.environ['ST_LLM_BASE_URL'];model=os.environ['ST_LLM_MODEL']
    os.environ.update(MAIN_LLM_PROVIDER='vllm',MODEL_NAME=model,INTENT_LLM_PROVIDER='vllm',INTENT_LLM_MODEL=model,
        CONVERSATION_LLM_PROVIDER='vllm',CONVERSATION_LLM_MODEL=model,EXPLAIN_LLM_PROVIDER='vllm',EXPLAIN_LLM_MODEL=model,
        VLLM_BASE_URL=base,VLLM_API_KEY=key)
    sys.path.insert(0,config['source_root'])
    import torch
    torch.set_num_threads(8)
    from agent.music_agent import MusicRecommendationAgent
    from services.recommendation_execution import RecommendationExecution,RecommendationModels,execution_scope
    from langchain_openai import ChatOpenAI
    common={'model':model,'base_url':base,'api_key':key,'timeout':80}
    intent=ChatOpenAI(**common,max_tokens=8192,temperature=.3)
    prose=ChatOpenAI(**common,max_tokens=4000,temperature=.7)
    # Reuse the existing real graph/dense index; this adapter never constructs a library.
    data=Path(config['data_dir']);data.mkdir(parents=True,exist_ok=True)
    os.chdir(data)
    with execution_scope(RecommendationExecution(web_search_enabled=False,
            models=RecommendationModels(main=prose,intent=intent,conversation=prose,explain=prose))):
        result=await asyncio.wait_for(MusicRecommendationAgent().get_recommendations(
            query=request['query'],user_id='studio-music-selection',
            client_context={'session_id':str(Path(request['output']).parent),'timezone':'Asia/Shanghai',
                'scene':'video_background_music_selection'}),timeout=180)
    def serialize(value):return value.model_dump()if hasattr(value,'model_dump')else str(value)
    Path(request['output']).write_text(json.dumps(result,ensure_ascii=False,indent=2,default=serialize)+'\n')


if __name__=='__main__':
    asyncio.run(run(json.loads(Path(sys.argv[1]).read_text())))
