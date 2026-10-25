"""Check oracle isolation, payload capture and immutable v3 registration."""
import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('v3_runner', ROOT/'scripts/replay/79e_run_p1_verifiable_tasks_v3.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class VerifiableRunnerTests(unittest.TestCase):
    def test_registration_rejects_output_budget_drift(self):
        cfg = runner.base.read(runner.CONFIG)
        result = runner.model_free_checks(cfg)
        self.assertEqual((result['measured_completions'],result['warmup_completions'],result['expected_cap_events']), (24,2,52))
        self.assertEqual(result['task_counts'], {'extract':8,'check':8,'organize':8})
        changed = deepcopy(cfg)
        changed['max_output_tokens']['background'] = 768
        with self.assertRaises(AssertionError):
            runner.model_free_checks(changed)

    def test_replay_projection_removes_all_offline_answers_and_facts(self):
        row = {'req_id':'v3-test', 'category':'interactive','prompt':'资料A=47。只返回A的值。',
            'input_tokens':40, 'input_token_bucket':0, 'input_token_range':[32,79], 'max_tokens':64,
            'token_ids_sha256':'test-token-sha','content_sha256':'test-content-sha','task_kind':'extract',
            'expected_answer':'OFFLINE_ONLY_DO_NOT_SEND', 'expected_lines':['OFFLINE_ONLY_DO_NOT_SEND'],
            'source_records':[{'id':'A','value':'47'}], 'left_records':[{'id':'A','value':'47'}],
            'right_records':[{'id':'A','value':'47'}], 'target_ids':['A'], 'evaluation_only':True,
            'oracle_evaluation_only':True, 'oracle':{'answer':'OFFLINE_ONLY_DO_NOT_SEND'},
            'unregistered_extra':'OFFLINE_ONLY_DO_NOT_SEND'}
        projected = runner.replay_row(row)
        self.assertFalse(runner.FORBIDDEN & projected.keys())
        self.assertNotIn('unregistered_extra',projected)
        self.assertEqual(projected['prompt'],row['prompt'])
        self.assertFalse(projected['cache_prompt'])
        payload = runner.completion_payload(projected)
        self.assertEqual(set(payload),runner.PAYLOAD_KEYS)
        self.assertNotIn('OFFLINE_ONLY_DO_NOT_SEND',json.dumps(payload))


class NativePayloadCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_frozen_client_http_payload_excludes_oracle_fields(self):
        captured = []
        async def handle(reader,writer):
            try:
                await reader.readline()
                headers = {}
                while True:
                    line = await reader.readline()
                    if line == b'\r\n':
                        break
                    key,value = line.decode().split(':',1)
                    headers[key.lower()] = value.strip()
                body = await reader.readexactly(int(headers['content-length']))
                captured.append(json.loads(body))
                final = {'content':'X','stop':True,'stop_type':'eos','truncated':False,
                    'tokens_evaluated':1,'tokens_predicted':1,
                    'timings':{'prompt_n':1,'cache_n':0,'predicted_n':1}}
                response = ('data: '+json.dumps(final)+'\n\n').encode()
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: '+str(len(response)).encode()+b'\r\nConnection: close\r\n\r\n'+response)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(handle,'127.0.0.1',0)
        port = server.sockets[0].getsockname()[1]
        row = {'req_id':'capture-v3','category':'interactive','arrival_offset_ms':0,
            'prompt':'事实A=47。返回值。','input_tokens':1,'max_tokens':64,
            'temperature':0,'ignore_eos':False,'cache_prompt':False,
            'expected_answer':'SECRET_EVALUATION_FIELD_123',
            'expected_lines':['SECRET_EVALUATION_FIELD_123'],
            'source_records':[{'id':'secret-id','value':'SECRET_EVALUATION_FIELD_123'}],
            'evaluation_only':True,'target_ids':['secret-id']}
        try:
            with tempfile.TemporaryDirectory() as folder:
                result = await runner.base.replay([row],Path(folder)/'capture',f'http://127.0.0.1:{port}',.01,2,2,50,3)
                self.assertEqual(result['terminal_counts']['completed'],1)
        finally:
            server.close()
            await server.wait_closed()
        self.assertEqual(len(captured),1)
        self.assertEqual(set(captured[0]),runner.PAYLOAD_KEYS)
        self.assertEqual(captured[0],runner.completion_payload(row))
        self.assertNotIn('SECRET_EVALUATION_FIELD_123',json.dumps(captured[0]))


if __name__ == '__main__':
    unittest.main()
