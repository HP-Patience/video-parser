"""Personal learning persistence: temporary libraries only, no model/network/download."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from zipfile import ZipFile
from fastapi import FastAPI
from fastapi.testclient import TestClient
from video_parser.services import library, study
from video_parser.web.study_routes import router

class StudyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        mock = patch.object(library, 'LIBRARY_ROOT', self.root/'library'); mock.start(); self.addCleanup(mock.stop)
        self.projects = [library.ensure({'platform':'bilibili','video_id':'BV1TaqYBcEJc','title':'学习',
            'source_url':f'https://www.bilibili.com/video/BV1TaqYBcEJc?p={i}'}) for i in (1,2)]
        source = self.root/'clip'; source.write_bytes(b'isolated media fixture')
        self.resource = 'video/test.mp4'
        for project in self.projects:
            library.import_file(project['id'], source, self.resource, 'video', {'media':{'format':{'duration':'20'}}})
        self.identifier = self.projects[0]['id']; self.note = 'a'*32
        app = FastAPI(); app.include_router(router)
        self.client = TestClient(app, base_url='http://127.0.0.1:15001', client=('127.0.0.1',12345))
        self.url = f'/api/workspace/projects/{self.identifier}/study'

    def test_progress_persists_clamps_and_does_not_mark_learned(self):
        before = library.get(self.identifier)
        saved = study.progress(self.identifier,self.resource,7.125,20,0)
        self.assertEqual(saved['position'],7.125)
        self.assertEqual(study.load(self.identifier,self.resource)['progress'],saved)
        self.assertEqual(study.load(self.projects[1]['id'],self.resource)['progress']['position'],0)
        self.assertEqual(study.progress(self.identifier,self.resource,25,20,1)['position'],19.99)
        after=library.get(self.identifier)
        self.assertEqual(after['resources'],before['resources']);self.assertFalse(after['learned'])

    def test_notes_crud_and_progress_do_not_overwrite_each_other(self):
        note = study.save_note(self.identifier,self.resource,self.note,5,'自己的理解',0)
        study.progress(self.identifier,self.resource,8,20,0)
        self.assertEqual(study.notes(self.identifier,self.resource),[note])
        self.assertEqual(study.notes(self.projects[1]['id'],self.resource),[])
        changed=study.save_note(self.identifier,self.resource,self.note,5,'修改后的理解',1)
        self.assertEqual(changed['revision'],2)
        self.assertEqual(study.load(self.identifier,self.resource)['progress']['position'],8)
        study.delete_note(self.identifier,self.resource,self.note,2)
        self.assertEqual(study.notes(self.identifier,self.resource),[])
        self.assertNotIn('修改后的理解',study.context(self.identifier,self.resource)[1].read_text(encoding='utf-8'))
        with self.assertRaises(study.Conflict):study.save_note(self.identifier,self.resource,self.note,5,'旧自动保存',0)

    def test_quiz_answers_persist_per_record(self):
        record = '20261028-161156-79e0ef47'
        self.assertEqual(study.quiz(self.identifier, self.resource, record), {'answers': {}, 'updated_at': None})
        saved = study.save_quiz(self.identifier, self.resource, record, {'0': 2, '3': 1})
        self.assertEqual(saved['answers'], {'0': 2, '3': 1})
        self.assertEqual(study.quiz(self.identifier, self.resource, record)['answers'], {'0': 2, '3': 1})
        # Each record owns its answers; notes and progress live in the same sidecar untouched.
        study.save_note(self.identifier, self.resource, self.note, 5, '笔记', 0)
        self.assertEqual(study.quiz(self.identifier, self.resource, '20261028-133813-b1bafcb1')['answers'], {})
        self.assertEqual(study.quiz(self.identifier, self.resource, record)['answers'], {'0': 2, '3': 1})
        self.assertEqual(len(study.notes(self.identifier, self.resource)), 1)
        # "重新作答" clears by replacing the whole map.
        self.assertEqual(study.save_quiz(self.identifier, self.resource, record, {})['answers'], {})
        self.assertEqual(study.quiz(self.identifier, self.resource, record)['answers'], {})

    def test_quiz_rejects_bad_ids_and_answers(self):
        for bad in ('', '../x', 'a' * 65, 'has space'):
            with self.assertRaises(ValueError):
                study.save_quiz(self.identifier, self.resource, bad, {})
        for bad in ({'0': 6}, {'0': True}, {'x': 1}, {'0': -1}, {str(i): 0 for i in range(201)}):
            with self.assertRaises(ValueError):
                study.save_quiz(self.identifier, self.resource, 'rec-1', bad)

    def test_sidecar_written_before_quizzes_still_reads(self):
        _, path, fingerprint, _ = study.context(self.identifier, self.resource)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'version': 1, 'project_id': self.identifier, 'resource': self.resource,
            'fingerprint': fingerprint, 'progress': {'position': 3.0, 'duration': 20.0, 'revision': 1}, 'notes': {}}), encoding='utf-8')
        self.assertEqual(study.load(self.identifier, self.resource)['progress']['position'], 3.0)
        self.assertEqual(study.quiz(self.identifier, self.resource, 'rec-1')['answers'], {})
        study.save_quiz(self.identifier, self.resource, 'rec-1', {'1': 0})
        self.assertEqual(study.quiz(self.identifier, self.resource, 'rec-1')['answers'], {'1': 0})
        self.assertEqual(study.load(self.identifier, self.resource)['progress']['position'], 3.0)

    def test_http_quiz_roundtrip(self):
        base = f'{self.url}/quiz/rec-1'
        self.assertEqual(self.client.get(base, params={'resource': self.resource}).json()['answers'], {})
        saved = self.client.post(base, json={'resource': self.resource, 'answers': {'2': 3}})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.client.get(base, params={'resource': self.resource}).json()['answers'], {'2': 3})
        self.assertEqual(self.client.post(base, json={'resource': self.resource, 'answers': {'2': 9}}).status_code, 400)
        self.assertEqual(self.client.post(f'{self.url}/quiz/bad%20id', json={'resource': self.resource, 'answers': {}}).status_code, 422)

    def test_conflicts_are_not_silent_overwrites(self):
        study.progress(self.identifier,self.resource,5,20,0)
        with self.assertRaises(study.Conflict):study.progress(self.identifier,self.resource,1,20,0)
        study.save_note(self.identifier,self.resource,self.note,5,'原文',0)
        with self.assertRaises(study.Conflict):study.save_note(self.identifier,self.resource,self.note,5,'旧版本',0)
        with self.assertRaises(study.Conflict):study.delete_note(self.identifier,self.resource,self.note,0)
        self.assertEqual(study.notes(self.identifier,self.resource)[0]['text'],'原文')

    def test_parallel_distinct_notes_survive(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda i: study.save_note(self.identifier,self.resource,f'{i:032x}',i,'独立笔记',0),range(1,5)))
        self.assertEqual(len(study.notes(self.identifier,self.resource)),4)

    def test_corrupt_file_is_preserved(self):
        path=study.context(self.identifier,self.resource)[1];path.parent.mkdir();path.write_text('{broken',encoding='utf-8')
        with self.assertRaises(ValueError):study.progress(self.identifier,self.resource,2,20,0)
        self.assertEqual(path.read_text(encoding='utf-8'),'{broken')

    def test_path_resource_and_time_boundaries(self):
        for resource in ('../project.json','C:/private.txt','video/unknown.mp4'):
            with self.assertRaises(ValueError):study.load(self.identifier,resource)
        for value in (True,-1,float('nan'),float('inf'),10**400):
            with self.assertRaises(ValueError):study.progress(self.identifier,self.resource,value,20,0)
        for text in ('','  ','长'*5001):
            with self.assertRaises(ValueError):study.save_note(self.identifier,self.resource,self.note,5,text,0)
        with self.assertRaises(ValueError):study.save_note(self.identifier,self.resource,self.note,21,'越界',0)
        with self.assertRaises(ValueError):study.progress(self.identifier,self.resource,1,20,True)

    def test_missing_changed_and_linked_resources(self):
        media=library.contained(library.get(self.identifier)['directory'],self.resource)
        media.write_bytes(b'changed')
        with self.assertRaises(ValueError):study.load(self.identifier,self.resource)
        media.unlink()
        with self.assertRaises(ValueError):study.load(self.identifier,self.resource)

    def test_http_validation_origin_and_conflict(self):
        url=self.url+'/progress';body={'resource':self.resource,'position':5,'duration':20,'expected_revision':0}
        self.assertEqual(self.client.post(url,json=body).status_code,200)
        self.assertEqual(self.client.post(url,json=body).status_code,409)
        self.assertEqual(self.client.get(url,params={'resource':self.resource}).json()['position'],5)
        self.assertIn('no-store',self.client.get(url,params={'resource':self.resource}).headers['cache-control'])
        for extra in ({'position':True},{'position':-1},{'duration':0},{'expected_revision':True},{'script':'no'}):
            self.assertEqual(self.client.post(url,json={**body,**extra}).status_code,422)
        self.assertEqual(self.client.post(url,json=body,headers={'Origin':'https://evil.invalid'}).status_code,403)
        self.assertEqual(self.client.post(url,content=json.dumps(body),headers={'Content-Type':'text/plain'}).status_code,415)
        self.assertEqual(self.client.get(self.url.replace(self.identifier,'bad')+'/progress',params={'resource':self.resource}).status_code,422)

    def test_http_note_roundtrip_and_delete(self):
        url=self.url+'/notes/'+self.note;body={'resource':self.resource,'time':5,'text':'<script>只是笔记</script>','expected_revision':0}
        self.assertEqual(self.client.post(url,json=body).status_code,200)
        self.assertEqual(self.client.get(self.url+'/notes',params={'resource':self.resource}).json()['notes'][0]['text'],body['text'])
        self.assertEqual(self.client.post(url+'/delete',json={'resource':self.resource,'expected_revision':1}).status_code,200)
        self.assertEqual(self.client.post(url,json=body).status_code,409)

    def test_archive_includes_notes_without_registering_as_ai(self):
        study.save_note(self.identifier,self.resource,self.note,5,'个人内容',0)
        with ZipFile(library.archive(self.identifier)) as archive:
            sidecars=[name for name in archive.namelist() if name.startswith('personal-learning/')]
            self.assertEqual(len(sidecars),1)
            self.assertIn('个人内容',archive.read(sidecars[0]).decode())
        self.assertEqual(len(library.get(self.identifier)['resources']),1)

    def test_failed_write_preserves_saved_note(self):
        original=study.save_note(self.identifier,self.resource,self.note,5,'已经保存的内容',0)
        with patch.object(library,'atomic_json',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):study.save_note(self.identifier,self.resource,self.note,5,'不能写入',1)
        self.assertEqual(study.notes(self.identifier,self.resource),[original])

    def test_analysis_records_do_not_own_personal_notes(self):
        original=study.save_note(self.identifier,self.resource,self.note,5,'自己的笔记',0)
        report=library.save_text(self.identifier,'analysis','新 AI 分析',{'video_resource':self.resource})
        library.remove(self.identifier,[report])
        self.assertEqual(study.notes(self.identifier,self.resource),[original])

    def test_quality_resources_and_note_limit(self):
        project=library.get(self.identifier);source=self.root/'quality';source.write_bytes(b'quality fixture')
        second=library.import_file(self.identifier,source,'video/720p.mp4','video',{'media':{'format':{'duration':'20'}}})
        study.save_note(self.identifier,self.resource,self.note,5,'原画质笔记',0)
        self.assertEqual(study.notes(self.identifier,second),[])
        self.assertEqual(study.load(self.identifier,second)['progress']['position'],0)
        with patch.object(study,'MAX_NOTES',1):
            with self.assertRaises(ValueError):study.save_note(self.identifier,self.resource,'b'*32,5,'超过数量',0)

if __name__=='__main__':unittest.main()
