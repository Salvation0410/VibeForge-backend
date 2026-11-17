"""显式隔离HTTP验收设施；只能由随机alias测试启动，不访问MySQL/供应商。"""
import argparse
import asyncio
import json
from pathlib import Path
import socket
import subprocess
import sys
from dataclasses import replace
import threading
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import uvicorn
from pymilvus import MilvusClient
from ai_service.config import Settings
from ai_service.app import create_app
from ai_service.api.knowledge_publication_schemas import PublicationContext, ReconcileRequest
from ai_service.infrastructure.knowledge_publication_store import PublicationStore
from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeStore, IndexedDocument, IndexedChunk
from ai_service.infrastructure.authoritative_knowledge import AuthoritativeKnowledgeStore
from ai_service.infrastructure.spring_knowledge_publication import SpringKnowledgePublicationClient
from ai_service.infrastructure.checkpoint import DisabledCheckpoint
from ai_service.orchestration.customer_service_rag import CustomerServiceRagService
from ai_service.models.base import CustomerServiceModelAnswer
from test_knowledge_revision_milvus_integration import LocalCoordinator, OwnedClient
from test_milvus_knowledge import lease
from test_customer_service_rag import FakeReranker
from conftest import FakeModel, FakeToolGateway

TOKEN='isolated-signing-secret'
def settings(alias,port):
    return Settings(_env_file=None,internal_bearer_token=TOKEN,spring_gateway_bearer_token=TOKEN,
        spring_gateway_base_url=f'http://127.0.0.1:{port}/internal/ai-tools',checkpoint_enabled=False,
        customer_service_rag_enabled=True,rag_publication_enabled=True,rag_reranker_provider='disabled',
        rag_embedding_dimension=3,rag_embedding_model='model-v1',milvus_uri='http://localhost:19530',
        milvus_token='',milvus_database='default',milvus_collection_alias=alias,
        frontend_skills_enabled=False,prompt_rewrite_enabled=False,langsmith_tracing=False)

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]

def serve(args):
    s=settings(args.alias,args.proxy_port);store=MilvusKnowledgeStore(s)
    control=SpringKnowledgePublicationClient(gateway_base_url=str(s.spring_gateway_base_url),bearer_token=TOKEN)
    class Embeddings:
        calls=0
        async def embed_query(self,question): self.calls+=1;return [0.1,0.2,0.9]
        async def embed_documents(self,texts,**kwargs):self.calls+=1;return [[0.1,0.2,0.9] for _ in texts]
    class Model(FakeModel):
        answer_calls=0
        async def answer_customer_service(self,question,contexts):
            self.answer_calls+=1
            if question=='fault':
                async with httpx.AsyncClient(trust_env=False) as c:
                    await c.post(f'http://127.0.0.1:{args.proxy_port}/control/fail-snapshot')
            return CustomerServiceModelAnswer(True,'隔离有据答案',(contexts[0].chunk_id,))
    embeddings=Embeddings();model=Model()
    service=CustomerServiceRagService(s,embeddings,AuthoritativeKnowledgeStore(s,store,PublicationStore(s,store),control),FakeReranker(),model)
    if args.prepare_mode:
        from test_knowledge_publication import Downloader
        path=args.report.parent/'manual.txt'
        app=create_app(settings=s,model=model,tool_gateway=FakeToolGateway(),checkpoint=DisabledCheckpoint(),
            knowledge_downloader=Downloader(path),embedding_provider=embeddings,reranker=FakeReranker())
    else:
        app=create_app(settings=s,model=model,tool_gateway=FakeToolGateway(),checkpoint=DisabledCheckpoint(),
        knowledge_etl_service=object(),mutation_coordinator=object(),customer_service_rag_service=service,
        knowledge_publication_client=control,reranker=FakeReranker(),
        knowledge_publication_preparer=SimpleNamespace(publications=PublicationStore(s,store)))
    @app.get('/test/counts')
    def counts():return dict(embeddings=embeddings.calls,model=model.answer_calls)
    uvicorn.run(app,host='127.0.0.1',port=args.python_port,log_level='error')

async def prepare_run(args):
    assert args.alias.startswith('rag_http_it_') and len(args.alias)==44
    s=settings(args.alias,args.spring_port);milvus=MilvusClient(uri=s.milvus_uri,timeout=20)
    owned=OwnedClient(milvus,args.alias);store=MilvusKnowledgeStore(s,client_factory=lambda **_:owned,mutation_coordinator=LocalCoordinator())
    children=[];proxy=None;state={'requests':0,'drop':True};attempts=[];port=free_port()
    try:
        context=PublicationContext(publication_id='bootstrap_'+'0'*32,base_epoch=0,source_collection=None,documents=[])
        permit_lease=lease('collection:'+args.alias,operation='REBUILD')
        async with store._mutation(permit_lease,scope=permit_lease.scope,operation='rebuild') as permit:
            initial=await PublicationStore(s,store).write(context,permit_lease,[],permit)
        (args.report.parent/'manual.txt').write_text('隔离知识说明',encoding='utf8')
        async with httpx.AsyncClient(trust_env=False,timeout=45,headers={'Authorization':'Bearer '+TOKEN}) as c:
            r=await c.post(f'http://127.0.0.1:{args.spring_port}/test/initialize',json=initial.model_dump(by_alias=True));r.raise_for_status()
        process=subprocess.Popen([sys.executable,__file__,'--serve','--prepare-mode','--alias',args.alias,'--proxy-port',str(args.spring_port),
            '--python-port',str(port),'--report',str(args.report)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0);children.append(process)
        async with httpx.AsyncClient(trust_env=False,timeout=2) as c:
            for _ in range(100):
                if process.poll() is not None:raise RuntimeError('PREPARE_CHILD_EXITED')
                try:
                    if (await c.get(f'http://127.0.0.1:{port}/health/live')).status_code==200:break
                except httpx.HTTPError:pass
                await asyncio.sleep(.1)
            else:raise RuntimeError('PREPARE_START_TIMEOUT')
        class Proxy(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
                with httpx.Client(trust_env=False,timeout=35) as c:
                    response=c.post(f'http://127.0.0.1:{port}'+self.path,content=raw,headers={'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json'})
                if 'prepare-publication' in self.path:
                    state['requests']+=1
                    # 仅在内存核对完整身份；租约proof和下载地址不进入日志或报告。
                    assert response.status_code==200, response.status_code
                    attempts.append((json.loads(raw),response.json()))
                    if state['drop'] and response.status_code==200:
                        state['drop']=False;self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
                self.send_response(response.status_code);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(response.content)
        proxy=ThreadingHTTPServer(('127.0.0.1',0),Proxy);threading.Thread(target=proxy.serve_forever,daemon=True).start()
        async with httpx.AsyncClient(trust_env=False,timeout=60,headers={'Authorization':'Bearer '+TOKEN}) as c:
            first=(await c.post(f'http://127.0.0.1:{args.spring_port}/test/worker',json={'port':proxy.server_port})).json()
            assert first['status']=='PENDING' and first['retryCount']==1,first
            counts=(await c.get(f'http://127.0.0.1:{port}/test/counts')).json();assert counts['embeddings']==1 and counts['model']==0,counts
            client=SpringKnowledgePublicationClient(gateway_base_url=f'http://127.0.0.1:{args.spring_port}',bearer_token=TOKEN)
            before=await client.snapshot();assert before.epoch==1 and before.collection_name==initial.candidate_collection
            validation=(await c.post(f'http://127.0.0.1:{args.spring_port}/internal/customer-service/knowledge-mutation-leases:validate',
                json=attempts[0][0]['mutation']['lease'])).json()
            assert validation['data']['verified'] is True and validation['data']['current'] is False
            # 清空进程内状态后恢复，仅依赖Spring anchor和Milvus完成证据。
            process.terminate();process.wait(10)
            process=subprocess.Popen([sys.executable,__file__,'--serve','--prepare-mode','--alias',args.alias,'--proxy-port',str(args.spring_port),
                '--python-port',str(port),'--report',str(args.report)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0);children.append(process)
            for _ in range(100):
                if process.poll() is not None:raise RuntimeError('PREPARE_RESTART_EXITED')
                try:
                    if (await c.get(f'http://127.0.0.1:{port}/health/live')).status_code==200:break
                except httpx.HTTPError:pass
                await asyncio.sleep(.1)
            else:raise RuntimeError('PREPARE_RESTART_TIMEOUT')
            second=(await c.post(f'http://127.0.0.1:{args.spring_port}/test/worker',json={'port':proxy.server_port})).json();assert second['status']=='SUCCEEDED',second
            counts=(await c.get(f'http://127.0.0.1:{port}/test/counts')).json();assert counts['embeddings']==0 and counts['model']==0,counts
            fact=await client.fact('task_1');assert fact.epoch==2
            names=[n for n in milvus.list_collections() if n.startswith(args.alias+'_pub_')]
            assert len(names)==3 and state['requests']==2 and not state['drop'],(names,state)
            first_receipt,second_receipt=attempts[0][1],attempts[1][1]
            assert first_receipt['operationId']!=second_receipt['operationId']
            assert first_receipt['fence']<second_receipt['fence']
            assert first_receipt['candidateCollection']!=second_receipt['candidateCollection']
            assert fact.receipt.model_dump(by_alias=True)==second_receipt
            after=await client.snapshot();assert after.collection_name==second_receipt['candidateCollection']
            assert first_receipt['candidateCollection'] in names
            # 未确认首候选保留，但数据库只发布第二attempt；旧候选没有alias激活权。
            assert attempts[0][0]['prepareAnchor']==attempts[1][0]['prepareAnchor']
            args.report.write_text(json.dumps(dict(prepareResponseDropped=not state['drop'],embeddingCalls=1+counts['embeddings'],candidates=len(names),
                prepareRequests=state['requests'],firstTaskState=first['status'],publishedEpoch=fact.epoch,paidCalls=0,
                oldLeaseRevoked=True,attemptIdentityIsolated=True,publishedSecondReceipt=True,oldHeadPreserved=True,
                restartedPrepareRecovered=True,persistedAnchorUnchanged=True),indent=2),encoding='utf8')
            await client.close()
    finally:
        for child in children:
            if child.poll() is None:child.terminate();child.wait(10)
        if proxy:proxy.shutdown();proxy.server_close()
        try:
            if args.alias in milvus.list_aliases().get('aliases',[]):milvus.drop_alias(args.alias)
            for name in milvus.list_collections():
                if name.startswith(args.alias+'_'):
                    assert args.alias.startswith('rag_http_it_');milvus.drop_collection(name)
            assert not any(n.startswith(args.alias+'_') for n in milvus.list_collections())
        finally:await store.close()

async def run(args):
    assert args.alias.startswith('rag_http_it_') and len(args.alias)==44
    s=settings(args.alias,args.spring_port);milvus=MilvusClient(uri=s.milvus_uri,timeout=20)
    owned=OwnedClient(milvus,args.alias);store=MilvusKnowledgeStore(s,client_factory=lambda **_:owned,mutation_coordinator=LocalCoordinator())
    state={'fail':False,'drop':True,'commits':0,'snapshots':0,'failedSnapshots':0}
    class Proxy(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):self.forward()
        def do_POST(self):self.forward()
        def forward(self):
            if self.path=='/control/fail-snapshot':
                state['fail']=True;self.send_response(200);self.end_headers();self.wfile.write(b'{}');return
            if self.path.endswith('/snapshot'):
                state['snapshots']+=1
                if state['fail']:
                    state['failedSnapshots']+=1
                    self.send_response(503);self.end_headers();self.wfile.write(b'{}');return
            raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
            with httpx.Client(trust_env=False,timeout=20) as c:
                response=c.request(self.command,f'http://127.0.0.1:{args.spring_port}'+self.path,content=raw,headers={'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json'})
            if self.path=='/test/commit':
                state['commits']+=1
                if state['drop'] and response.status_code==200:
                    state['drop']=False;self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
            self.send_response(response.status_code);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(response.content)
    proxy=ThreadingHTTPServer(('127.0.0.1',0),Proxy);thread=threading.Thread(target=proxy.serve_forever,daemon=True);thread.start()
    proxy_port=proxy.server_port;children=[]
    async def start_instance():
        port=free_port()
        process=subprocess.Popen([sys.executable,__file__,'--serve','--alias',args.alias,'--proxy-port',str(proxy_port),'--python-port',str(port)],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
        children.append(process)
        async with httpx.AsyncClient(trust_env=False,timeout=2) as c:
            for _ in range(100):
                if process.poll() is not None:raise RuntimeError('PYTHON_CHILD_EXITED')
                try:
                    if (await c.get(f'http://127.0.0.1:{port}/health/live')).status_code==200:return port
                except httpx.HTTPError:pass
                await asyncio.sleep(0.1)
        raise RuntimeError('PYTHON_START_TIMEOUT')
    try:
        async with httpx.AsyncClient(trust_env=False,timeout=20,headers={'Authorization':'Bearer '+TOKEN}) as c:
            context=PublicationContext.model_validate((await c.get(f'http://127.0.0.1:{args.spring_port}/test/context')).json())
            identity=context.documents[0];text='隔离知识说明';import hashlib
            chunk=IndexedChunk('1:1:0',0,text,'lines 1-1',hashlib.sha256(text.encode()).hexdigest(),(0.1,0.2,0.9))
            doc=IndexedDocument(document_id=identity.document_id,document_version=1,etl_version='2',embedding_model_version='model-v1',
                file_name=identity.file_name,file_type=identity.file_type.lower(),content_hash=identity.sha256,index_revision=2,activation_revision=1,chunks=(chunk,))
            info=(await c.get(f'http://127.0.0.1:{args.spring_port}/test/lease-identity')).json()
            permit_lease=replace(lease('collection:'+args.alias,operation='REBUILD'),operation_id=info['operationId'],fence=info['fence'])
            async with store._mutation(permit_lease,scope=permit_lease.scope,operation='rebuild') as permit:
                receipt=await PublicationStore(s,store).write(context,permit_lease,[doc],permit)
            try:
                await c.post(f'http://127.0.0.1:{proxy_port}/test/commit',json=receipt.model_dump(by_alias=True));raise AssertionError('response was not dropped')
            except httpx.RemoteProtocolError:pass
            control=SpringKnowledgePublicationClient(gateway_base_url=f'http://127.0.0.1:{proxy_port}',bearer_token=TOKEN)
            fact=await control.fact('task_1');assert fact.receipt==receipt and fact.epoch==2
            await c.post(f'http://127.0.0.1:{args.spring_port}/test/revoke')
            again=await c.post(f'http://127.0.0.1:{proxy_port}/test/commit',json=receipt.model_dump(by_alias=True));assert again.json()['epoch']==2
            port=await start_instance()
            response=await c.post(f'http://127.0.0.1:{port}/internal/v1/customer-service/answers',json={'question':'fault','requestId':'fault-1'})
            answer=response.json();assert not answer['answered'] and not answer['sources'],answer
            counts=(await c.get(f'http://127.0.0.1:{port}/test/counts')).json();assert counts=={'embeddings':1,'model':1},counts
            assert state['failedSnapshots']==1, state
            state['fail']=False;children[0].terminate();children[0].wait(10)
            port=await start_instance()
            answer=(await c.post(f'http://127.0.0.1:{port}/internal/v1/customer-service/answers',json={'question':'normal','requestId':'normal-1'})).json()
            assert answer['answered'] and answer['sources'],answer
            reconcile=dict(collectionAlias=args.alias,collectionName=receipt.candidate_collection,publicationId='task_1',epoch=2)
            second_port=await start_instance()
            for instance_port in (port,second_port):
                result=(await c.post(f'http://127.0.0.1:{instance_port}/internal/v1/customer-service/knowledge:reconcile-alias',json=reconcile)).json();assert result.get('reconciled'),result
            assert milvus.describe_alias(args.alias)['collection_name']==receipt.candidate_collection
            assert state['commits']==2
            report=dict(commitResponseDropped=True,inflightAnswerDiscarded=True,restartedInstanceConfirmed=True,mirrorConverged=True,
                        commitRequests=state['commits'],faultModelCalls=counts['model'],faultEmbeddingCalls=counts['embeddings'],snapshotRequests=state['snapshots'],
                        failedSnapshotRequests=state['failedSnapshots'],automaticPaidRetries=0,paidCalls=0)
            args.report.write_text(json.dumps(report,indent=2),encoding='utf8');await control.close()
    finally:
        for child in children:
            if child.poll() is None:child.terminate();child.wait(10)
        proxy.shutdown();proxy.server_close()
        try:
            if args.alias in milvus.list_aliases().get('aliases',[]):milvus.drop_alias(args.alias)
            for name in owned.created:
                assert name.startswith(args.alias+'_');milvus.drop_collection(name)
            assert not any(milvus.has_collection(name) for name in owned.created)
        finally:await store.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--alias',required=True);p.add_argument('--spring-port',type=int);p.add_argument('--report',type=Path)
    p.add_argument('--prepare-mode',action='store_true')
    p.add_argument('--serve',action='store_true');p.add_argument('--proxy-port',type=int);p.add_argument('--python-port',type=int);args=p.parse_args()
    if args.serve:serve(args)
    elif args.prepare_mode:asyncio.run(prepare_run(args))
    else:asyncio.run(run(args))
