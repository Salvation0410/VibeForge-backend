"""显式随机命名空间HTTP故障验收，计数与退出仅在测试进程内注入。"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import uvicorn
from pymilvus import MilvusClient
from ai_service.app import create_app
from ai_service.api.knowledge_publication_schemas import CandidateRequest, PublicationContext
from ai_service.infrastructure.checkpoint import DisabledCheckpoint
from ai_service.infrastructure.knowledge_publication_store import PublicationStore
from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeStore
from http_publication_runner import settings, free_port, TOKEN
from test_knowledge_publication import Downloader
from test_knowledge_revision_milvus_integration import LocalCoordinator, OwnedClient
from test_milvus_knowledge import lease
from test_customer_service_rag import FakeReranker
from conftest import FakeModel, FakeToolGateway


def serve(args):
    s=settings(args.alias,args.proxy_port)
    class Download(Downloader):
        async def download(self,*a,**kw):
            if args.fault=='exit-grant':os._exit(79)
            return await super().download(*a,**kw)
    class Embeddings:
        async def embed_documents(self,texts,**kw):
            # 模拟供应商发送前持久预占，退出后父进程仍能读取计数。
            count=int(args.count.read_text()) if args.count.exists() else 0
            with args.count.open('w',encoding='utf8') as output:
                output.write(str(count+1));output.flush();os.fsync(output.fileno())
            return [[.1,.2,.9] for _ in texts]
    app=create_app(settings=s,model=FakeModel(),tool_gateway=FakeToolGateway(),checkpoint=DisabledCheckpoint(),
                   knowledge_downloader=Download(args.report.parent/'manual.txt'),embedding_provider=Embeddings(),reranker=FakeReranker())
    if args.fault=='exit-embedding':
        # 供应商替身已返回，但候选尚未写入；硬退出不执行finally。
        async def exit_write(*a,**kw):os._exit(79)
        PublicationStore.write=exit_write
    uvicorn.run(app,host='127.0.0.1',port=args.python_port,log_level='error')


async def run(args):
    assert args.alias.startswith('rag_http_it_') and len(args.alias)==44
    s=settings(args.alias,args.spring_port);raw=MilvusClient(uri=s.milvus_uri,timeout=20)
    owned=OwnedClient(raw,args.alias);store=MilvusKnowledgeStore(s,client_factory=lambda **_:owned,mutation_coordinator=LocalCoordinator())
    publications=PublicationStore(s,store);children=[];proxy=None
    state={'dropped':False,'begin':0,'complete':0}
    args.count=args.report.parent/'count.txt'
    try:
        empty=PublicationContext(publication_id='bootstrap_'+'0'*32,base_epoch=0,source_collection=None,documents=[])
        first_lease=lease('collection:'+args.alias,operation='REBUILD')
        async with store._mutation(first_lease,scope=first_lease.scope,operation='rebuild') as permit:
            initial=await publications.write(empty,first_lease,[],permit)
        (args.report.parent/'manual.txt').write_text('隔离知识说明',encoding='utf8')
        headers={'Authorization':'Bearer '+TOKEN}
        async with httpx.AsyncClient(trust_env=False,timeout=35,headers=headers) as c:
            response=await c.post(f'http://127.0.0.1:{args.spring_port}/test/fault-initialize',json=initial.model_dump(by_alias=True));response.raise_for_status()
            request=(await c.get(f'http://127.0.0.1:{args.spring_port}/test/prepare-request')).json()
        body=CandidateRequest.model_validate(request)
        class Proxy(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):self.forward()
            def do_GET(self):self.forward()
            def forward(self):
                content=self.rfile.read(int(self.headers.get('Content-Length','0')))
                with httpx.Client(trust_env=False,timeout=30) as c:
                    response=c.request(self.command,f'http://127.0.0.1:{args.spring_port}'+self.path,content=content,headers={**headers,'Content-Type':'application/json'})
                if self.path.endswith('/prepare-execution'):
                    status=json.loads(content)['status'];state['begin' if status=='BEGIN' else 'complete']+=1
                    target='BEGIN' if args.fault=='begin-loss' else 'COMPLETE'
                    if args.fault in {'begin-loss','complete-loss'} and status==target and not state['dropped']:
                        assert response.status_code==200 and response.json()['code']==0
                        state['dropped']=True;self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
                self.send_response(response.status_code);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(response.content)
        proxy=ThreadingHTTPServer(('127.0.0.1',0),Proxy);threading.Thread(target=proxy.serve_forever,daemon=True).start()
        port=free_port()
        async def start(fault):
            child=subprocess.Popen([sys.executable,__file__,'--serve','--alias',args.alias,'--fault',fault,'--proxy-port',str(proxy.server_port),
                                    '--python-port',str(port),'--report',str(args.report),'--count',str(args.count)],
                                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
            children.append(child)
            async with httpx.AsyncClient(trust_env=False,timeout=2) as c:
                for _ in range(100):
                    if child.poll() is not None:raise RuntimeError('CHILD_START_FAILED')
                    try:
                        if (await c.get(f'http://127.0.0.1:{port}/health/live')).status_code==200:return child
                    except httpx.HTTPError:pass
                    await asyncio.sleep(.1)
            raise RuntimeError('CHILD_START_TIMEOUT')
        child=await start(args.fault)
        url=f'http://127.0.0.1:{port}/internal/v1/customer-service/knowledge:prepare-publication'
        async with httpx.AsyncClient(trust_env=False,timeout=35,headers=headers) as c:
            try:
                result=await c.post(url,json=request)
                assert args.fault.endswith('loss') and result.status_code==503,(args.fault,result.status_code)
            except (httpx.RemoteProtocolError, httpx.ReadError):
                assert args.fault.startswith('exit-')
            if args.fault.startswith('exit-'):
                await asyncio.to_thread(child.wait,10);assert child.returncode==79
            else:
                child.terminate();await asyncio.to_thread(child.wait,10)
            observation=(await c.get(f'http://127.0.0.1:{args.spring_port}/test/execution-observation')).json()
            assert observation['status']==('COMPLETE' if args.fault=='complete-loss' else 'STARTED')
            before=int(args.count.read_text()) if args.count.exists() else 0
            await start('none')
            again=await c.post(url,json=request)
            name=publications.candidate_name(body.context,body.mutation.lease.operation_id,body.mutation.lease.fence)
            if args.fault=='complete-loss':
                assert again.status_code==200
                receipt,_=await publications.completed(name)
                assert again.json()==receipt.model_dump(by_alias=True)
            else:
                assert again.status_code==503 and again.json()['error']['code']=='KNOWLEDGE_PREPARE_BUSY'
                assert not raw.has_collection(name)
            after=int(args.count.read_text()) if args.count.exists() else 0
            assert before==after==(1 if args.fault in {'exit-embedding','complete-loss'} else 0)
            snapshot=(await c.get(f'http://127.0.0.1:{args.spring_port}/internal/customer-service/knowledge-publications/snapshot')).json()['data']
            assert snapshot['epoch']==1 and snapshot['collectionName']==initial.candidate_collection
            assert state['begin']==(1 if args.fault=='complete-loss' else 2)
            args.report.write_text(json.dumps(dict(fault=args.fault,embeddingCalls=after,noAdditionalEmbedding=True,oldHeadPreserved=True,
                executionState=observation['status'],beginRequests=state['begin'],completeRequests=state['complete'],responseDropped=state['dropped'],
                hardExitVerified=args.fault.startswith('exit-'),paidCalls=0),indent=2),encoding='utf8')
    finally:
        for child in children:
            if child.poll() is None:child.terminate();await asyncio.to_thread(child.wait,10)
        if proxy:proxy.shutdown();proxy.server_close()
        for name in raw.list_collections():
            if name.startswith(args.alias+'_'):raw.drop_collection(name)
        assert not any(n.startswith(args.alias+'_') for n in raw.list_collections())
        await store.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--alias',required=True);p.add_argument('--fault',required=True,choices=['begin-loss','exit-grant','exit-embedding','complete-loss','none'])
    p.add_argument('--spring-port',type=int);p.add_argument('--proxy-port',type=int);p.add_argument('--python-port',type=int)
    p.add_argument('--report',type=Path,required=True);p.add_argument('--count',type=Path);p.add_argument('--serve',action='store_true');args=p.parse_args()
    if args.serve:serve(args)
    else:asyncio.run(run(args))
